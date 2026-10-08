"""Saved plans resolve per model and remain immutable once scheduled."""
from copy import deepcopy
from datetime import timedelta

import yaml
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from dcim.models import Device, DeviceType
from ipam.models import IPAddress
from users.models import ObjectPermission

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import DeviceTypeProfile, JobProfile, UpgradeJob
from netbox_discovery.profile_views import JobProfileForm, UPGRADE_TEMPLATE
from .test_upgrades import UpgradeFixture, PROFILE


class JobProfileTest(UpgradeFixture, TestCase):
    def test_clearpass_approval_checkbox_roundtrip(self):
        data = {'name': 'ClearPass NTP', 'kind': 'remediate', 'features': ['ntp'], 'remediation_mode': 'add',
                'allow_clearpass_cluster_changes': True}
        form = JobProfileForm(data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save()
        self.assertTrue(profile.plan['allow_clearpass_cluster_changes'])
        self.assertTrue(JobProfileForm(instance=profile).initial['allow_clearpass_cluster_changes'])
        form = JobProfileForm({**data, 'allow_clearpass_cluster_changes': False}, instance=profile, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('allow_clearpass_cluster_changes', form.save().plan)

    def setUp(self):
        from netbox_compliance.tests.factories import feature_standards
        feature_standards()
        from netbox_compliance.models import ConfigStandard
        ConfigStandard.objects.create(name='Banner', check_type='netops',
                                      definition_yaml='banner: {motd: true}', allow_enforce=True)
        self.setup_data()
        self.upgrade = JobProfile.objects.create(name='Campus upgrade', kind='upgrade', plan=deepcopy(PROFILE))
        self.remediation = JobProfile.objects.create(name='Campus configuration', kind='remediate',
                                                     plan={'features': ['ntp', 'syslog'], 'mode': 'add'})
        self.assignment = DeviceTypeProfile.objects.create(device_type=self.device.device_type,
                                                           upgrade_profile=self.upgrade,
                                                           remediation_profile=self.remediation)
        self.defaults = {key: value for key, value in self.data.items() if key != 'profile'}

    def test_defaults_resolve_by_operation_and_freeze_settings(self):
        for operation, expected in (('audit', self.upgrade), ('stage', self.upgrade),
                                    ('upgrade', self.upgrade), ('remediate', self.remediation)):
            with self.subTest(operation=operation):
                job = queue.schedule(self.user, {**self.defaults, 'operation': operation})[0]
                self.assertEqual(job.profile, expected.plan)
                self.assertEqual(job.profile_name, expected.name)
        self.upgrade.plan['target_version'] = '17.18.5'
        self.upgrade.save()
        self.assertEqual(UpgradeJob.objects.filter(operation='upgrade').get().profile['target_version'], '17.18.4')
        next_job = queue.schedule(self.user, self.defaults)[0]
        self.assertEqual(next_job.profile['target_version'], '17.18.5')

    def test_mixed_models_use_separate_defaults(self):
        dtype = DeviceType.objects.create(manufacturer=self.device.device_type.manufacturer,
                                           model='C9500-24Y4C', slug='c9500-24y4c')
        other = Device.objects.create(name='core1', site=self.site, role=self.role, device_type=dtype,
                                       primary_ip4=IPAddress.objects.create(address='192.0.2.5/24'))
        profile = JobProfile.objects.create(name='Core upgrade', kind='upgrade',
                                             plan={**PROFILE, 'models': [dtype.model], 'target_version': '17.18.6'})
        DeviceTypeProfile.objects.create(device_type=dtype, upgrade_profile=profile)
        jobs = queue.schedule(self.user, self.defaults)
        self.assertEqual({j.device_id: j.profile['target_version'] for j in jobs},
                         {self.device.pk: '17.18.4', other.pk: '17.18.6'})
        self.assertEqual(len({j.batch_id for j in jobs}), 1)

    def test_missing_wrong_kind_and_incompatible_profile_block_whole_batch(self):
        self.assignment.upgrade_profile = None
        self.assignment.save()
        with self.assertRaisesRegex(queue.QueueError, 'no accessible'):
            queue.schedule(self.user, self.defaults)
        with self.assertRaisesRegex(queue.QueueError, 'matching this operation'):
            queue.schedule(self.user, {**self.defaults, 'profile_source': 'saved',
                                       'saved_profile': self.remediation.pk})
        self.upgrade.plan['models'] = ['different-model']
        self.upgrade.save()
        with self.assertRaisesRegex(queue.QueueError, 'is not assigned to model'):
            queue.schedule(self.user, {**self.defaults, 'profile_source': 'saved', 'saved_profile': self.upgrade.pk})
        self.assertFalse(UpgradeJob.objects.exists())

    def test_custom_settings_still_work_and_cannot_be_silently_ignored(self):
        job = queue.schedule(self.user, self.data)[0]
        self.assertEqual(job.profile, PROFILE)
        with self.assertRaisesRegex(queue.QueueError, 'cannot be combined'):
            queue.schedule(self.user, {**self.data, 'profile_source': 'model'})

    def test_one_missing_model_aborts_the_batch(self):
        dtype = DeviceType.objects.create(manufacturer=self.device.device_type.manufacturer,
                                          model='Unassigned', slug='unassigned')
        Device.objects.create(name='unassigned', site=self.site, role=self.role, device_type=dtype,
                              primary_ip4=IPAddress.objects.create(address='192.0.2.7/24'))
        with self.assertRaisesRegex(queue.QueueError, 'no accessible'):
            queue.schedule(self.user, self.defaults)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_pending_edit_can_resolve_defaults_without_changing_other_jobs(self):
        first, second = queue.schedule(self.user, self.defaults)[0], queue.schedule(self.user, self.defaults)[0]
        self.upgrade.plan['target_version'] = '17.18.5'
        self.upgrade.save()
        edited = queue.edit_pending(self.user, first.pk, {**self.defaults, 'profile_source': 'model',
                                    'last_updated': first.last_updated, 'description': ''})
        self.assertEqual(edited.profile['target_version'], '17.18.5')
        second.refresh_from_db()
        self.assertEqual(second.profile['target_version'], '17.18.4')

    def test_profile_and_assignment_validation(self):
        self.assignment.upgrade_profile = self.remediation
        with self.assertRaises(ValidationError):
            self.assignment.full_clean()
        self.upgrade.kind = 'remediate'
        self.upgrade.plan = self.remediation.plan
        with self.assertRaisesRegex(ValidationError, 'Remove model assignments'):
            self.upgrade.full_clean()
        for plan in ({'features': [[]], 'mode': 'add'}, {'features': [], 'mode': 'add'}):
            with self.assertRaises(queue.QueueError):
                queue.validate_remediation(plan)

    def test_profile_and_assignment_visibility_are_required(self):
        user = get_user_model().objects.create_user('profile-reader')
        for model, constraints in ((Device, None), (DeviceTypeProfile, None), (JobProfile, {'id': self.remediation.pk})):
            perm = ObjectPermission.objects.create(name=f'profiles-{model.__name__}', actions=['view'], constraints=constraints)
            perm.object_types.add(ContentType.objects.get_for_model(model))
            perm.users.add(user)
        with self.assertRaisesRegex(queue.QueueError, 'no accessible'):
            queue.prepare(user, {**self.defaults, 'operation': 'audit'})

    def test_create_profiles_and_assignment_over_api(self):
        response = self.client.post(reverse('plugins-api:netbox_discovery-api:jobprofile-list'),
                                    {'name': 'API remediation', 'kind': 'remediate',
                                     'plan': {'features': ['ntp'], 'mode': 'replace'}}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.patch(reverse('plugins-api:netbox_discovery-api:devicetypeprofile-detail',
                                            args=[self.assignment.pk]),
                                     {'remediation_profile': response.data['id'], 'replace_existing': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch(reverse('plugins-api:netbox_discovery-api:devicetypeprofile-detail',
                                            args=[self.assignment.pk]),
                                     {'upgrade_profile': self.remediation.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.patch(reverse('plugins-api:netbox_discovery-api:jobprofile-detail',
                                            args=[self.upgrade.pk]),
                                     {'kind': 'remediate', 'plan': self.remediation.plan}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_api_schedule_preview_and_snapshot(self):
        url = reverse('plugins-api:netbox_discovery-api:upgradejob-schedule')
        payload = {**self.defaults, 'scheduled_at': self.data['scheduled_at'].isoformat(),
                   'start_before': self.data['start_before'].isoformat(), 'profile_source': 'model'}
        response = self.client.post(url, {**payload, 'preview': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['devices'][0]['profile_name'], self.upgrade.name)
        self.assertFalse(UpgradeJob.objects.exists())
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(UpgradeJob.objects.get().profile, self.upgrade.plan)

    def test_profile_forms_build_and_validate_plans(self):
        form = JobProfileForm(data={'name': 'New upgrade', 'kind': 'upgrade', 'upgrade_yaml': yaml.safe_dump(PROFILE)})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().plan, {key: value for key, value in PROFILE.items() if key != 'models'})
        form = JobProfileForm(data={'name': 'Fix time', 'kind': 'remediate', 'features': ['ntp'], 'remediation_mode': 'add'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().plan, {'features': ['ntp'], 'mode': 'add'})
        for data in ({'name': 'Bad', 'kind': 'upgrade', 'upgrade_yaml': '['},
                     {'name': 'Empty', 'kind': 'remediate'}):
            form = JobProfileForm(data=data)
            self.assertFalse(form.is_valid())

    def test_new_upgrade_profile_has_safe_yaml_template(self):
        form = JobProfileForm()
        self.assertEqual(form['upgrade_yaml'].value(), UPGRADE_TEMPLATE)
        plan = yaml.safe_load(UPGRADE_TEMPLATE)
        self.assertNotIn('models', plan)
        self.assertEqual(plan['starting_versions'], ['17.9.4a', '17.12.3'])
        self.assertEqual(plan['target_version'], '17.12.4')
        self.assertIn('md5', plan)
        self.assertIn('  - ', UPGRADE_TEMPLATE)
        submitted = JobProfileForm(data={'name': 'Incomplete', 'kind': 'upgrade',
                                         'upgrade_yaml': UPGRADE_TEMPLATE})
        self.assertFalse(submitted.is_valid())
        self.assertIn('upgrade_yaml', submitted.errors)

    def test_yaml_editor_preserves_saved_and_submitted_values(self):
        form = JobProfileForm(instance=self.upgrade)
        text = form['upgrade_yaml'].value()
        self.assertEqual(yaml.safe_load(text), {key: value for key, value in PROFILE.items() if key != 'models'})
        self.assertIn('starting_versions:\n  - ', text)
        self.assertEqual(form.initial['device_types'], [self.device.device_type_id])
        self.assertNotIn('Example only', text)
        custom = JobProfileForm(initial={'upgrade_yaml': 'custom: initial'})
        self.assertEqual(custom['upgrade_yaml'].value(), 'custom: initial')
        for text in ('', 'models: [broken'):
            submitted = JobProfileForm(data={'name': 'Broken', 'kind': 'upgrade', 'upgrade_yaml': text})
            self.assertFalse(submitted.is_valid())
            self.assertEqual(submitted['upgrade_yaml'].value(), text)
        widget = form.fields['upgrade_yaml'].widget
        self.assertIn('font-monospace', widget.attrs['class'])
        self.assertEqual(widget.attrs['wrap'], 'off')
        self.assertEqual(widget.attrs['spellcheck'], 'false')

    def test_running_config_option_round_trip_and_frozen_assignment(self):
        data = {'name': self.upgrade.name, 'kind': 'upgrade', 'upgrade_yaml': yaml.safe_dump(PROFILE),
                'skip_running_config_check': 'on', 'device_types': [self.device.device_type_id]}
        form = JobProfileForm(data=data, instance=self.upgrade)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertIs(saved.plan['skip_running_config_check'], True)
        edit = JobProfileForm(instance=saved)
        self.assertTrue(edit.initial['skip_running_config_check'])
        self.assertNotIn('skip_running_config_check', yaml.safe_load(edit.initial['upgrade_yaml']))
        job = queue.schedule(self.user, self.defaults)[0]
        self.assertTrue(queue.assignment(job)['profile']['skip_running_config_check'])
        data.pop('skip_running_config_check')
        form = JobProfileForm(data=data, instance=saved)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.save().plan.get('skip_running_config_check', False))
        job.refresh_from_db()
        self.assertTrue(job.profile['skip_running_config_check'])

    def test_running_config_option_api_validation(self):
        url = reverse('plugins-api:netbox_discovery-api:jobprofile-detail', args=[self.upgrade.pk])
        for value in ('false', 1, None):
            response = self.client.patch(url, {'plan': {**PROFILE, 'skip_running_config_check': value}}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.patch(url, {'plan': {**PROFILE, 'skip_running_config_check': True}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIs(response.data['plan']['skip_running_config_check'], True)

    def test_ui_pages_and_model_default_schedule(self):
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        response = client.post(reverse('plugins:netbox_discovery:jobprofile_edit', args=[self.remediation.pk]),
                               {'name': self.remediation.name, 'kind': 'remediate',
                                'device_types': [self.device.device_type_id],
                                'features': ['ntp', 'banner'], 'remediation_mode': 'replace'})
        self.assertEqual(response.status_code, 302)
        self.remediation.refresh_from_db()
        self.assertEqual(self.remediation.plan, {'features': ['ntp', 'banner'], 'mode': 'replace'})
        for name, args in (('jobprofile_list', []), ('jobprofile_add', []), ('jobprofile', [self.upgrade.pk]),
                           ('jobprofile_edit', [self.remediation.pk]), ('devicetypeprofile_list', []),
                           ('devicetypeprofile_add', []), ('devicetypeprofile', [self.assignment.pk]),
                           ('devicetypeprofile_edit', [self.assignment.pk])):
            with self.subTest(page=name):
                response = client.get(reverse('plugins:netbox_discovery:' + name, args=args))
                self.assertEqual(response.status_code, 200)
        now = timezone.now()
        response = client.post(reverse('plugins:netbox_discovery:upgradejob_add'),
                               {'device_type': self.device.device_type_id, 'operation': 'remediate',
                                'profile_source': 'model', 'scheduled_at': now.isoformat(),
                                'start_before': (now + timedelta(hours=1)).isoformat(), 'schedule': 'yes'})
        self.assertEqual(response.status_code, 302, response.context['form'].errors if response.context else None)
        self.assertEqual(UpgradeJob.objects.get().profile, self.remediation.plan)

    def test_profile_model_picker_updates_existing_assignment_without_changing_remediation(self):
        data = {'name': 'Replacement upgrade', 'kind': 'upgrade', 'upgrade_yaml': yaml.safe_dump(PROFILE),
                'device_types': [self.device.device_type_id]}
        form = JobProfileForm(data=data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('Campus upgrade', str(form.errors))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.upgrade_profile_id, self.upgrade.pk)
        data.update(replace_existing='on', assignment_confirmation=form['assignment_confirmation'].value())
        form = JobProfileForm(data=data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.upgrade_profile_id, profile.pk)
        self.assertEqual(self.assignment.remediation_profile_id, self.remediation.pk)
        self.assertEqual(profile.resolved_plan()['models'], [self.device.device_type.model])
        self.assertEqual(self.upgrade.resolved_plan()['models'], [])
        self.assertNotIn('models', profile.plan)

    def test_conflict_confirmation_does_not_authorize_a_different_replacement(self):
        data = {'name': 'Replacement upgrade', 'kind': 'upgrade', 'upgrade_yaml': yaml.safe_dump(PROFILE),
                'device_types': [self.device.device_type_id], 'replace_existing': 'on'}
        first = JobProfileForm(data=data, user=self.user)
        self.assertFalse(first.is_valid())
        data['assignment_confirmation'] = first['assignment_confirmation'].value()
        other = JobProfile.objects.create(name='Changed in the meantime', kind='upgrade', plan=PROFILE)
        self.assignment.upgrade_profile = other
        self.assignment.save()
        second = JobProfileForm(data=data, user=self.user)
        self.assertFalse(second.is_valid())
        self.assertIn(other.name, str(second.errors))

    def test_unselecting_models_preserves_other_default_and_frozen_jobs(self):
        job = queue.schedule(self.user, self.defaults)[0]
        form = JobProfileForm(data={'name': self.upgrade.name, 'kind': 'upgrade',
                                    'upgrade_yaml': yaml.safe_dump(PROFILE), 'device_types': []},
                              instance=self.upgrade, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.upgrade_profile_id)
        self.assertEqual(self.assignment.remediation_profile_id, self.remediation.pk)
        job.refresh_from_db()
        self.assertEqual(job.profile['models'], PROFILE['models'])
        with self.assertRaisesRegex(queue.QueueError, 'no accessible'):
            queue.schedule(self.user, self.defaults)

    def test_model_assignment_edit_and_model_rename_update_generated_plan(self):
        dtype = DeviceType.objects.create(manufacturer=self.device.device_type.manufacturer,
                                          model='C9350-24P', slug='c9350-24p')
        DeviceTypeProfile.objects.create(device_type=dtype, upgrade_profile=self.upgrade)
        self.assertEqual(set(self.upgrade.resolved_plan()['models']), {'C9350-24P', 'C9350-48P'})
        form = JobProfileForm(instance=self.upgrade)
        self.assertEqual(set(form.initial['device_types']), {dtype.pk, self.device.device_type_id})
        self.assertNotIn('models:', form.initial['upgrade_yaml'])
        dtype.model = 'C9350-24U'
        dtype.save()
        self.assertIn('C9350-24U', self.upgrade.resolved_plan()['models'])
        self.assertNotIn('C9350-24P', self.upgrade.resolved_plan()['models'])

    def test_legacy_yaml_models_are_not_an_independent_allowlist(self):
        self.upgrade.plan['models'] = ['WRONG-MODEL']
        self.upgrade.save()
        job = queue.schedule(self.user, self.defaults)[0]
        self.assertEqual(job.profile['models'], [self.device.device_type.model])

    def test_profile_api_sets_and_clears_the_shared_assignment(self):
        dtype = DeviceType.objects.create(manufacturer=self.device.device_type.manufacturer,
                                          model='C9350-24P', slug='c9350-24p')
        url = reverse('plugins-api:netbox_discovery-api:jobprofile-detail', args=[self.upgrade.pk])
        response = self.client.patch(url, {'device_types': [self.device.device_type_id, dtype.pk]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data['plan']['models']), {'C9350-24P', 'C9350-48P'})
        self.assertTrue(DeviceTypeProfile.objects.filter(device_type=dtype, upgrade_profile=self.upgrade).exists())
        response = self.client.patch(url, {'description': 'Keep selections'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(dtype.pk, response.data['device_types'])
        response = self.client.patch(url, {'device_types': [self.device.device_type_id]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(DeviceTypeProfile.objects.filter(device_type=dtype).exists())

    def test_profile_api_conflicts_require_explicit_confirmed_replacement(self):
        profile = JobProfile.objects.create(name='Other upgrade', kind='upgrade', plan=PROFILE)
        url = reverse('plugins-api:netbox_discovery-api:jobprofile-detail', args=[profile.pk])
        payload = {'device_types': [self.device.device_type_id]}
        response = self.client.patch(url, payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.upgrade_profile_id, self.upgrade.pk)
        payload.update(replace_existing=True, assignment_confirmation=response.data['assignment_confirmation'][0])
        response = self.client.patch(url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.upgrade_profile_id, profile.pk)

    def test_profile_api_create_assigns_selected_models_and_ignores_typed_models(self):
        dtype = DeviceType.objects.create(manufacturer=self.device.device_type.manufacturer,
                                          model='C9350-24P', slug='c9350-24p')
        response = self.client.post(reverse('plugins-api:netbox_discovery-api:jobprofile-list'),
                                    {'name': 'New model upgrade', 'kind': 'upgrade',
                                     'plan': {**PROFILE, 'models': ['WRONG-MODEL']},
                                     'device_types': [dtype.pk]}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        profile = JobProfile.objects.get(pk=response.data['id'])
        self.assertNotIn('models', profile.plan)
        self.assertEqual(response.data['plan']['models'], [dtype.model])
        self.assertEqual(dtype.job_profiles.upgrade_profile_id, profile.pk)
        url = reverse('plugins-api:netbox_discovery-api:jobprofile-detail', args=[profile.pk])
        response = self.client.patch(url, {'device_types': []}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['device_types'], [])
        self.assertEqual(response.data['plan']['models'], [])
        self.assertFalse(DeviceTypeProfile.objects.filter(device_type=dtype).exists())

    def test_profile_permission_does_not_grant_assignment_permission(self):
        user = get_user_model().objects.create_user('profile-editor')
        for model, actions in ((DeviceType, ['view']), (JobProfile, ['view', 'change']),
                               (DeviceTypeProfile, ['view'])):
            perm = ObjectPermission.objects.create(name=f'assign-{model.__name__}', actions=actions)
            perm.object_types.add(ContentType.objects.get_for_model(model))
            perm.users.add(user)
        form = JobProfileForm(data={'name': self.upgrade.name, 'kind': 'upgrade',
                                    'upgrade_yaml': yaml.safe_dump(PROFILE), 'device_types': []},
                              instance=self.upgrade, user=user)
        self.assertFalse(form.is_valid())
        self.assertIn('cannot change', str(form.errors))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.upgrade_profile_id, self.upgrade.pk)

    def test_model_assignment_api_replacement_requires_confirmation(self):
        profile = JobProfile.objects.create(name='Other upgrade', kind='upgrade', plan=PROFILE)
        url = reverse('plugins-api:netbox_discovery-api:devicetypeprofile-detail', args=[self.assignment.pk])
        response = self.client.patch(url, {'upgrade_profile': profile.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.patch(url, {'upgrade_profile': profile.pk, 'replace_existing': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(profile.resolved_plan()['models'], [self.device.device_type.model])
