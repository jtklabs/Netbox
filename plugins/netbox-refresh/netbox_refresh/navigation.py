from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

lifecycle = PluginMenuItem(
    link='plugins:netbox_refresh:modellifecycle_list',
    link_text='Model Lifecycles',
    permissions=['netbox_refresh.view_modellifecycle'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_refresh:modellifecycle_add',
            title='Add',
            icon_class='mdi mdi-plus-thick',
            permissions=['netbox_refresh.add_modellifecycle'],
        ),
        PluginMenuButton(
            link='plugins:netbox_refresh:modellifecycle_bulk_import',
            title='Import',
            icon_class='mdi mdi-upload',
            permissions=['netbox_refresh.add_modellifecycle'],
        ),
    ),
)

report = PluginMenuItem(
    link='plugins:netbox_refresh:refresh_report',
    link_text='Refresh Report',
    permissions=['netbox_refresh.view_modellifecycle'],
)

menu = PluginMenu(
    label='Hardware Lifecycle',
    groups=(('Lifecycle', (lifecycle, report)),),
    icon_class='mdi mdi-calendar-clock',
)
