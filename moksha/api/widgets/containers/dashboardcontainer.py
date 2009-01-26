from moksha.api.widgets.layout.layout import layout_js, layout_css, ui_core_js, ui_draggable_js, ui_droppable_js, ui_sortable_js 

from tw.api import Widget
from tw.jquery import jquery_js
from moksha.lib.helpers import eval_app_config
from tg import config

class AppListWidget(Widget):
    template = 'mako:moksha.api.widgets.containers.templates.layout_applist'
    properties = ['layout', 'category']
    
    def update_params(self, d): 
        super(AppListWidget, self).update_params(d)
        
        # we want to error out if there is no category
        c = d['category']
        if isinstance(c, basestring):
            for cat in d['layout']:
                if cat['label'] == c:
                    d['category'] = cat
                    break

applist_widget = AppListWidget('applist');

class DashboardContainer(Widget):
    template = 'mako:moksha.api.widgets.containers.templates.dashboardcontainer'
    css = []
    javascript = [jquery_js, layout_js, ui_core_js, ui_draggable_js,
                  ui_droppable_js, ui_sortable_js]
    config_key = None
    layout = []

    def update_params(self, d): 
        super(DashboardContainer, self).update_params(d)
        layout = eval_app_config(config.get(self.config_key, "None"))

        if not layout:
            if isinstance(self.layout, basestring):
                layout = eval_app_config(self.layout)
            else:
                layout = self.layout
                
        # Filter out any None's in the layout which signify apps which are
        # not allowed to run with the current session's authorization level
        
        l = []
        for category in layout:
            if category == None:
                continue
            
            app_list = category['apps']
            category['apps'] = filter(lambda x: x, app_list)
            l.append(category)
    
        d['layout'] = l
        d['applist_widget'] = applist_widget
        return d