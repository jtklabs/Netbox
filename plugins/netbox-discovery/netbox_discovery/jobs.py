from netbox.jobs import JobRunner

from netbox_discovery import upgrade_prestage


class PrestageJob(JobRunner):
    """Recurring image prestage from the enabled prestage policies — Operations > Jobs."""

    class Meta:
        name = 'Upgrade image prestage'

    def run(self, *args, **kwargs):
        return upgrade_prestage.run(logger=self.logger.info)
