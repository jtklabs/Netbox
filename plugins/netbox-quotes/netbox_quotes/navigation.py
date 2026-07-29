from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

vendors = PluginMenuItem(
    link='plugins:netbox_quotes:quotevendor_list',
    link_text='Vendors',
    permissions=['netbox_quotes.view_quotevendor'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_quotes:quotevendor_add',
            title='Add',
            icon_class='mdi mdi-plus-thick',
            permissions=['netbox_quotes.add_quotevendor'],
        ),
    ),
)
quotes = PluginMenuItem(
    link='plugins:netbox_quotes:quote_list',
    link_text='Quotes',
    permissions=['netbox_quotes.view_quote'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_quotes:quote_add',
            title='Add',
            icon_class='mdi mdi-plus-thick',
            permissions=['netbox_quotes.add_quote'],
        ),
    ),
)
lines = PluginMenuItem(
    link='plugins:netbox_quotes:quoteline_list',
    link_text='Quote Lines',
    permissions=['netbox_quotes.view_quoteline'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_quotes:quoteline_bulk_import',
            title='Import',
            icon_class='mdi mdi-upload',
            permissions=['netbox_quotes.add_quoteline'],
        ),
    ),
)

menu = PluginMenu(
    label='Support Quotes',
    groups=(('Quotes', (vendors, quotes, lines)),),
    icon_class='mdi mdi-file-document-outline',
)
