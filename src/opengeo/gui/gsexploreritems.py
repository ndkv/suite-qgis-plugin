import os
from PyQt4 import QtGui,QtCore, QtWebKit
from PyQt4.QtCore import *
from opengeo.qgis import layers as qgislayers
from opengeo.core.store import DataStore
from opengeo.core.resource import Coverage, FeatureType
from opengeo.geoserver.gwc import Gwc, GwcLayer, SeedingStatusParsingError
from opengeo.gui.catalogdialog import DefineCatalogDialog
from opengeo.core.style import Style
from opengeo.core.layer import Layer
from opengeo.gui.styledialog import AddStyleToLayerDialog, StyleFromLayerDialog
from opengeo.qgis.catalog import OGCatalog
from opengeo.gui.exploreritems import TreeItem
from opengeo.gui.groupdialog import LayerGroupDialog
from opengeo.gui.workspacedialog import DefineWorkspaceDialog
from opengeo.gui.gwclayer import SeedGwcLayerDialog, EditGwcLayerDialog
from opengeo.core.layergroup import UnsavedLayerGroup
from opengeo.gui.qgsexploreritems import QgsLayerItem, QgsGroupItem,\
    QgsStyleItem
from opengeo.geoserver.catalog import FailedRequestError
from opengeo.gui.pgexploreritems import PgTableItem

class GsTreeItem(TreeItem):
    
    def parentCatalog(self):        
        item  = self            
        while item is not None:                    
            if isinstance(item, GsCatalogItem):
                return item.element                           
            item = item.parent()            
        return None   
    
    def catalogs(self):
        item  = self            
        while item is not None:                    
            if isinstance(item, GsCatalogsItem):
                return item._catalogs                           
            item = item.parent()            
        return None
    
    def parentWorkspace(self):        
        item  = self            
        while item is not None:                    
            if isinstance(item, GsWorkspaceItem):
                return item.element                           
            item = item.parent()            
        return None   
                 
    def getDefaultWorkspace(self):                            
        workspaces = self.parentCatalog().get_workspaces()
        if workspaces:
            return self.parentCatalog().get_default_workspace()
        else:
            return None  
        
    def deleteElements(self, selected):                
        elements = []
        unused = []
        for item in selected:
            elements.append(item.element)
            if isinstance(item, GsStoreItem):
                for idx in range(item.childCount()):
                    subitem = item.child(idx)
                    elements.insert(0, subitem.element)
            elif isinstance(item, GsLayerItem):
                uniqueStyles = self.uniqueStyles(item.element)
                for style in uniqueStyles:
                    if style.name == item.element.name:
                        unused.append(style)      
        toUpdate = set(item.parent() for item in selected)                
        self.explorer.progress.setMaximum(len(elements))
        progress = 0        
        dependent = self.getDependentElements(elements)
                
        if dependent:
            msg = "The following elements depend on the elements to delete\nand will be deleted as well:\n\n"
            for e in dependent:
                msg += "-" + e.name + "(" + e.__class__.__name__ + ")\n\n"
            msg += "Do you really want to delete all these elements?"                   
            reply = QtGui.QMessageBox.question(None, "Delete confirmation",
                                               msg, QtGui.QMessageBox.Yes | 
                                               QtGui.QMessageBox.No, QtGui.QMessageBox.No)
            if reply == QtGui.QMessageBox.No:
                return
            toDelete = set()
            for e in dependent:                
                items = self.explorer.tree.findAllItems(e);                
                toUpdate.update(set(item.parent() for item in items))
                toDelete.update(items)
            toUpdate = toUpdate - toDelete
        
                
        unusedToUpdate = set() 
        for e in unused:                
            items = self.explorer.tree.findAllItems(e); 
            unusedToUpdate.add(item.parent())                       
        toUpdate.update(unusedToUpdate)
        
        elements[0:0] = dependent 
        elements.extend(unused)      
        for element in elements:
            self.explorer.progress.setValue(progress)    
            if isinstance(element, GwcLayer):
                self.explorer.run(element.delete,
                     element.__class__.__name__ + " '" + element.name + "' correctly deleted",
                     [])                      
            else:                                     
                self.explorer.run(element.catalog.delete,
                     element.__class__.__name__ + " '" + element.name + "' correctly deleted",
                     [], 
                     element, isinstance(element, Style))  
            progress += 1
        self.explorer.progress.setValue(progress)
        for item in toUpdate:
            item.refreshContent()
        self.explorer.progress.setValue(0)
    
    def uniqueStyles(self, layer):
        '''returns the styles used by a layer that are not used by any other layer'''
        unique = []
        allUsedStyles = set()
        catalog = layer.catalog
        layers = catalog.get_layers()
        for lyr in layers:
            if lyr.name == layer.name:
                continue
            for style in lyr.styles:
                allUsedStyles.add(style.name)
            allUsedStyles.add(lyr.default_style.name)
        for style in layer.styles:
            if style.name not in allUsedStyles:
                unique.append(style)
        if layer.default_style not in allUsedStyles:
            unique.append(layer.default_style)
        return unique
            
    def getDependentElements(self, elements):
        dependent = []
        for element in elements:
            if isinstance(element, Layer):
                groups = element.catalog.get_layergroups()
                for group in groups:                    
                    for layer in group.layers:
                        if layer == element.name:
                            dependent.append(group)
                            break                    
            elif isinstance(element, (FeatureType, Coverage)):
                layers = element.catalog.get_layers()
                for layer in layers:
                    if layer.resource.name == element.name:
                        dependent.append(layer)     
            elif isinstance(element, Style):
                layers = element.catalog.get_layers()                
                for layer in layers:
                    if layer.default_style.name == element.name:
                        dependent.append(layer)                         
                    else:
                        for style in layer.styles:                            
                            if style.name == element.name:
                                dependent.append(layer)
                                break
                                                                                    
        if dependent:
            subdependent = self.getDependentElements(dependent)
            if subdependent:
                dependent[0:0] = subdependent
        return dependent
                                     
    
class GsCatalogsItem(GsTreeItem):    
    def __init__(self): 
        self._catalogs = {}
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/geoserver.png")        
        GsTreeItem.__init__(self, None, icon, "GeoServer catalogs")        
                 
    def populate(self):
        for name, catalog in self._catalogs.iteritems():                    
            item = self.getGeoServerCatalogItem(catalog, name)
            self.addChild(item)

    def contextMenuActions(self, explorer):
        self.explorer = explorer
        createCatalogAction = QtGui.QAction("New catalog...", explorer)
        createCatalogAction.triggered.connect(self.addGeoServerCatalog)
        return [createCatalogAction]
                    
    def addGeoServerCatalog(self):         
        dlg = DefineCatalogDialog()
        dlg.exec_()
        cat = dlg.getCatalog()        
        if cat is not None:   
            name = dlg.getName()
            i = 2
            while name in self._catalogs.keys():
                name = dlg.getName() + "_" + str(i)
                i += 1                                 
            item = self.getGeoServerCatalogItem(cat, name)
            if item is not None:
                self._catalogs[name] = cat
                self.addChild(item)
        
        
    def getGeoServerCatalogItem(self, cat, name):    
        QtGui.QApplication.setOverrideCursor(QtGui.QCursor(Qt.WaitCursor))
        try:    
            geoserverItem = GsCatalogItem(cat, name)
            geoserverItem.populate()
            QtGui.QApplication.restoreOverrideCursor()
            self.explorer.setInfo("Catalog '" + name + "' correctly created")
            return geoserverItem
        except Exception, e:            
            QtGui.QApplication.restoreOverrideCursor()
            self.explorer.setInfo("Could not create catalog:" + str(e), 1)   
     
            
class GsLayersItem(GsTreeItem): 
    def __init__(self): 
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/layer.png")
        GsTreeItem.__init__(self, None, icon, "Layers")
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDropEnabled) 
            
    def populate(self):
        layers = self.parentCatalog().get_layers()
        for layer in layers:
            layerItem = GsLayerItem(layer)            
            layerItem.populate()    
            self.addChild(layerItem)       
    
    def acceptDroppedItem(self, explorer, item):            
        if isinstance(item, GsLayerItem):
            catalog = self.parentCatalog()
            workspace = self.getDefaultWorkspace()
            toUpdate = []
            if workspace is not None:
                publishDraggedLayer(explorer, item.element, workspace)
                toUpdate.append(explorer.tree.findAllItems(catalog)[0])  
            return toUpdate  
        elif isinstance(item, QgsGroupItem):                
            catalog = self.parentCatalog()
            if catalog is None:
                return
            workspace = self.parentWorkspace()
            if workspace is None:
                workspace = self.getDefaultWorkspace()
            publishDraggedGroup(explorer, item, catalog, workspace)
            return explorer.tree.findAllItems(catalog)
        elif isinstance(item, QgsLayerItem):
            catalog = self.parentCatalog()
            workspace = self.getDefaultWorkspace()
            toUpdate = []
            if workspace is not None:
                publishDraggedLayer(explorer, item.element, workspace)
                toUpdate.append(explorer.tree.findAllItems(catalog)[0])  
            return toUpdate  
                        
class GsGroupsItem(GsTreeItem): 
    def __init__(self): 
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/group.gif")
        GsTreeItem.__init__(self, None, icon, "Groups")
           
        
    def populate(self):
        groups = self.parentCatalog().get_layergroups()
        for group in groups:
            groupItem = GsGroupItem(group)
            groupItem.populate()                                
            self.addChild(groupItem)    
            
    def acceptDroppedItem(self, explorer, item):                    
        if isinstance(item, QgsGroupItem):                
            catalog = self.parentCatalog()
            if catalog is None:
                return
            workspace = self.parentWorkspace()
            if workspace is None:
                workspace = self.getDefaultWorkspace()
            publishDraggedGroup(explorer, item, catalog, workspace)
            return explorer.tree.findAllItems(catalog)       
    
    def contextMenuActions(self, explorer):
        self.explorer = explorer                
        createGroupAction = QtGui.QAction("New group...", explorer)
        createGroupAction.triggered.connect(self.createGroup)
        return [createGroupAction]
    
    def createGroup(self):
        dlg = LayerGroupDialog(self.parentCatalog())
        dlg.exec_()
        group = dlg.group
        if group is not None:
            self.explorer.run(self.parentCatalog().save,
                     "Group '" + group.name + "' correctly created",
                     [self],
                     group)
     
        
class GsWorkspacesItem(GsTreeItem): 
    def __init__(self): 
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/workspace.png")
        GsTreeItem.__init__(self, None, icon, "Workspaces")  
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDropEnabled)        
    
    def populate(self):
        cat = self.parentCatalog()
        try:
            defaultWorkspace = cat.get_default_workspace()
            defaultWorkspace.fetch()
            defaultName = defaultWorkspace.dom.find('name').text
        except:
            defaultName = None             
        workspaces = cat.get_workspaces()
        for workspace in workspaces:
            workspaceItem = GsWorkspaceItem(workspace, workspace.name == defaultName)
            workspaceItem.populate()
            self.addChild(workspaceItem) 
    
    def acceptDroppedItem(self, explorer, item):
        if isinstance(item, QgsGroupItem):                
            catalog = self.parentCatalog()
            if catalog is None:
                return
            workspace = self.getDefaultWorkspace()
            publishDraggedGroup(explorer, item, catalog, workspace)
            return explorer.tree.findAllItems(catalog)
        elif isinstance(item, QgsLayerItem):
            catalog = self.parentCatalog()
            workspace = self.getDefaultWorkspace()
            toUpdate = []
            if workspace is not None:
                publishDraggedLayer(explorer, item.element, workspace)
                toUpdate.append(explorer.tree.findAllItems(catalog)[0])  
            return toUpdate   
        elif isinstance(item, PgTableItem):
            catalog = self.parentCatalog()
            workspace = self.getDefaultWorkspace()
            toUpdate = []
            if workspace is not None:
                publishDraggedTable(explorer, item.element, workspace)
                toUpdate.append(explorer.tree.findAllItems(catalog)[0])  
            return toUpdate        
                            
    def contextMenuActions(self, explorer):
        self.explorer = explorer
        createWorkspaceAction = QtGui.QAction("New workspace...", explorer)
        createWorkspaceAction.triggered.connect(self.createWorkspace)
        return [createWorkspaceAction]
    
    def createWorkspace(self):
        dlg = DefineWorkspaceDialog() 
        dlg.exec_()            
        if dlg.name is not None:
            self.explorer.run(self.parentCatalog().create_workspace, 
                    "Workspace '" + dlg.name + "' correctly created",
                    [self],
                    dlg.name, dlg.uri)
                 
class GsStylesItem(GsTreeItem): 
    def __init__(self ): 
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/style.png")
        GsTreeItem.__init__(self, None, icon, "Styles")
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDragEnabled) 
                    
    def populate(self):
        styles = self.parentCatalog().get_styles()
        for style in styles:
            styleItem = GsStyleItem(style, False)                
            self.addChild(styleItem)

    def acceptDroppedItem(self, explorer, item):
        if isinstance(item, QgsLayerItem):
            catalog = self.parentCatalog()
            workspace = self.getDefaultWorkspace()
            toUpdate = []
            if workspace is not None:
                publishDraggedLayer(explorer, item.element, workspace)
                toUpdate.append(explorer.tree.findAllItems(catalog)[0])  
            return toUpdate  
        
    def contextMenuActions(self, explorer):
        self.explorer = explorer
        createStyleFromLayerAction = QtGui.QAction("New style from QGIS layer...", explorer)
        createStyleFromLayerAction.triggered.connect(self.createStyleFromLayer)
        return [createStyleFromLayerAction] 
           
    
    def createStyleFromLayer(self):  
        dlg = StyleFromLayerDialog(self.catalogs().keys())
        dlg.exec_()      
        if dlg.layer is not None:
            ogcat = OGCatalog(self.catalogs()[dlg.catalog])        
            self.explorer.run(ogcat.publish_style, 
                     "Style correctly created from layer '" + dlg.layer + "'",
                     [self],
                     dlg.layer, dlg.name, True)


class GsCatalogItem(GsTreeItem): 
    def __init__(self, catalog, name): 
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/geoserver.png")
        GsTreeItem.__init__(self, catalog, icon, name) 
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDragEnabled) 
        
    def populate(self):        
        self.workspacesItem = GsWorkspacesItem()                              
        self.addChild(self.workspacesItem)  
        self.workspacesItem.populate()
        self.layersItem = GsLayersItem()                                      
        self.addChild(self.layersItem)
        self.layersItem.populate()
        self.groupsItem = GsGroupsItem()                                    
        self.addChild(self.groupsItem)
        self.groupsItem.populate()
        self.stylesItem = GsStylesItem()                        
        self.addChild(self.stylesItem)
        self.stylesItem.populate()      
        self.gwcItem = GwcLayersItem()                        
        self.addChild(self.gwcItem)
        self.gwcItem.populate()

    def acceptDroppedItem(self, explorer, item):
        if isinstance(item, QgsStyleItem):                    
            publishDraggedStyle(item.element.name(), self) 
            return [self]   
        elif isinstance(item, QgsGroupItem):                
            catalog = self.element                        
            workspace = self.getDefaultWorkspace()
            publishDraggedGroup(explorer, item, catalog, workspace)
            return [self]
        elif isinstance(item, QgsLayerItem):
            catalog = self.element
            workspace = self.getDefaultWorkspace()                        
            publishDraggedLayer(explorer, item.element, workspace)            
            return [self]
        
    def contextMenuActions(self, explorer):
        self.explorer = explorer
        removeCatalogAction = QtGui.QAction("Remove", explorer)
        removeCatalogAction.triggered.connect(self.removeCatalog)
        return[removeCatalogAction] 
        
    def removeCatalog(self):
        del self.catalogs()[self.text(0)]
        parent = self.parent()        
        parent.takeChild(parent.indexOfChild(self))   
        
           
                        
                                
class GsLayerItem(GsTreeItem): 
    def __init__(self, layer): 
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/layer.png")
        GsTreeItem.__init__(self, layer, icon)  
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable 
                      | QtCore.Qt.ItemIsDropEnabled | QtCore.Qt.ItemIsDragEnabled)       
        
    def populate(self):
        layer = self.element
        for style in layer.styles:
            styleItem = GsStyleItem(style, False)
            self.addChild(styleItem)
        if layer.default_style is not None:
            styleItem = GsStyleItem(layer.default_style, True)                    
            self.addChild(styleItem)  
            
    def acceptDroppedItem(self, explorer, item):
        if isinstance(item, (GsStyleItem, QgsStyleItem)):                                                        
            addDraggedStyleToLayer(explorer, item, self)
            return [self] 
        elif isinstance(item, GsLayerItem):
            destinationItem = self.parent()
            toUpdate = []
            if isinstance(destinationItem, GsGroupItem):
                addDraggedLayerToGroup(explorer, item.element, destinationItem)
                toUpdate.append(destinationItem)
            return toUpdate
        elif isinstance(item, QgsLayerItem):
            catalog = self.parentCatalog()
            workspace = self.getDefaultWorkspace()
            toUpdate = []
            if workspace is not None:
                publishDraggedLayer(explorer, item.element, workspace)
                toUpdate.append(explorer.tree.findAllItems(catalog)[0])  
            return toUpdate  
                
    def contextMenuActions(self, explorer):
        self.explorer = explorer
        actions = []
        if isinstance(self.parent(), GsGroupItem):
            layers = self.parent().element.layers
            count = len(layers)
            idx = layers.index(self.element.name)
            removeLayerFromGroupAction = QtGui.QAction("Remove layer from group", explorer)            
            removeLayerFromGroupAction.setEnabled(count > 1)
            removeLayerFromGroupAction.triggered.connect(self.removeLayerFromGroup)
            actions.append(removeLayerFromGroupAction)                                                
            moveLayerUpInGroupAction = QtGui.QAction("Move up", explorer)            
            moveLayerUpInGroupAction.setEnabled(count > 1 and idx > 0)
            moveLayerUpInGroupAction.triggered.connect(self.moveLayerUpInGroup)
            actions.append(moveLayerUpInGroupAction)
            moveLayerDownInGroupAction = QtGui.QAction("Move down", explorer)            
            moveLayerDownInGroupAction.setEnabled(count > 1 and idx < count - 1)
            moveLayerDownInGroupAction.triggered.connect(self.moveLayerDownInGroup)
            actions.append(moveLayerDownInGroupAction)
            moveLayerToFrontInGroupAction = QtGui.QAction("Move to front", explorer)            
            moveLayerToFrontInGroupAction.setEnabled(count > 1 and idx > 0)
            moveLayerToFrontInGroupAction.triggered.connect(self.moveLayerToFrontInGroup)
            actions.append(moveLayerToFrontInGroupAction)
            moveLayerToBackInGroupAction = QtGui.QAction("Move to back", explorer)            
            moveLayerToBackInGroupAction.setEnabled(count > 1 and idx < count - 1)
            moveLayerToBackInGroupAction.triggered.connect(self.moveLayerToBackInGroup)
            actions.append(moveLayerToBackInGroupAction)
        else:
            addStyleToLayerAction = QtGui.QAction("Add style to layer...", explorer)
            addStyleToLayerAction.triggered.connect(self.addStyleToLayer)                    
            actions.append(addStyleToLayerAction)   
            deleteLayerAction = QtGui.QAction("Delete", None)
            deleteLayerAction.triggered.connect(self.deleteLayer)
            actions.append(deleteLayerAction)                                
            addLayerAction = QtGui.QAction("Add to current QGIS project", explorer)
            addLayerAction.triggered.connect(self.addLayerToProject)
            actions.append(addLayerAction)    
            
        return actions
    
    def multipleSelectionContextMenuActions(self, explorer, selected):
        self.explorer = explorer
        deleteSelectedAction = QtGui.QAction("Delete", explorer)
        deleteSelectedAction.triggered.connect(lambda: self.deleteElements(selected))
        createGroupAction = QtGui.QAction("Create group...", explorer)
        createGroupAction.triggered.connect(lambda: self.createGroupFromLayers(selected))        
        return [deleteSelectedAction, createGroupAction]
                 
            
    def createGroupFromLayers(self, selected):        
        name, ok = QtGui.QInputDialog.getText(None, "Group name", "Enter the name of the group to create")        
        if not ok:
            return
        catalog = self.element.catalog
        catalogItem = self.explorer.tree.findAllItems(catalog)[0]
        groupsItem = catalogItem.groupsItem
        layers = [item.element for item in selected]
        styles = [layer.default_style.name for layer in layers]
        layerNames = [layer.name for layer in layers]
        #TODO calculate bounds
        bbox = None
        group =  UnsavedLayerGroup(catalog, name, layerNames, styles, bbox)
                
        self.explorer.run(self.parentCatalog().save,
                     "Group '" + name + "' correctly created",
                     [groupsItem],
                     group)
                    
    def deleteLayer(self):
        self.deleteElements([self])
            
    def removeLayerFromGroup(self):
        group = self.parent().element
        layers = group.layers
        styles = group.styles
        idx = group.layers.index(self.element.name)
        del layers[idx]
        del styles[idx]
        group.dirty.update(layers = layers, styles = styles)
        self.explorer.run(self.parentCatalog().save, 
                 "Layer '" + self.element.name + "' correctly removed from group '" + group.name +"'",
                 [self.parent()],
                 group)

    def moveLayerDownInGroup(self):
        group = self.parent().element
        layers = group.layers
        styles = group.styles
        idx = group.layers.index(self.element.name)
        tmp = layers [idx + 1]
        layers[idx + 1] = layers[idx]
        layers[idx] = tmp  
        tmp = styles [idx + 1]
        styles[idx + 1] = styles[idx]
        styles[idx] = tmp          
        group.dirty.update(layers = layers, styles = styles)
        self.explorer.run(self.parentCatalog().save, 
                 "Layer '" + self.element.name + "' correctly moved down in group '" + group.name +"'",
                 [self.parent()],
                 group)        
    
    def moveLayerToFrontInGroup(self):
        group = self.parent().element
        layers = group.layers
        styles = group.styles
        idx = group.layers.index(self.element.name)
        tmp = layers[idx]
        del layers[idx]
        layers.insert(0, tmp)        
        tmp = styles [idx]
        del styles[idx]
        styles.insert(0, tmp)          
        group.dirty.update(layers = layers, styles = styles)
        self.explorer.run(self.parentCatalog().save, 
                 "Layer '" + self.element.name + "' correctly moved to front in group '" + group.name +"'",
                 [self.parent()],
                 group)
    
    def moveLayerToBackInGroup(self):
        group = self.parent().element
        layers = group.layers
        styles = group.styles
        idx = group.layers.index(self.element.name)
        tmp = layers[idx]
        del layers[idx]
        layers.append(tmp)        
        tmp = styles [idx]
        del styles[idx]
        styles.append(tmp)          
        group.dirty.update(layers = layers, styles = styles)
        self.explorer.run(self.parentCatalog().save, 
                 "Layer '" + self.element.name + "' correctly moved to back in group '" + group.name +"'",
                 [self.parent()],
                 group)
                     
    def moveLayerUpInGroup(self):
        group = self.parent().element
        layers = group.layers
        styles = group.styles
        idx = group.layers.index(self.element.name)
        tmp = layers [idx - 1]
        layers[idx - 1] = layers[idx]
        layers[idx] = tmp  
        tmp = styles [idx - 1]
        styles[idx - 1] = styles[idx]
        styles[idx] = tmp          
        group.dirty.update(layers = layers, styles = styles)
        self.explorer.run(self.parentCatalog().save, 
                 "Layer '" + self.element.name + "' correctly moved up in group '" + group.name +"'",
                 [self.parent()],
                 group)    
        
            
    def addStyleToLayer(self):
        cat = self.parentCatalog()
        dlg = AddStyleToLayerDialog(cat)
        dlg.exec_()
        if dlg.style is not None:
            layer = self.element
            styles = layer.styles            
            if dlg.default:
                default = layer.default_style
                styles.append(default)
                layer.styles = styles
                layer.default_style = dlg.style                 
            else:
                styles.append(dlg.style)
                layer.styles = styles 
            self.explorer.run(cat.save, 
                     "Style '" + dlg.style.name + "' correctly added to layer '" + layer.name + "'",
                     [self],
                     layer)  
            
    def addLayerToProject(self):
        #Using threads here freezes the QGIS GUI
        cat = OGCatalog(self.parentCatalog()) 
        cat.addLayerToProject(self.element.name) 
        self.explorer.setInfo("Layer '" + self.element.name + "' correctly added to QGIS project")                        

class GsGroupItem(GsTreeItem): 
    def __init__(self, group): 
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/group.gif")
        GsTreeItem.__init__(self, group, icon)
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable 
                      | QtCore.Qt.ItemIsDropEnabled)  
        
    def populate(self):
        layers = self.element.catalog.get_layers()
        layersDict = {layer.name : layer for layer in layers}
        groupLayers = self.element.layers
        if groupLayers is None:
            return
        for layer in groupLayers:
            layerItem = GsLayerItem(layersDict[layer])                    
            self.addChild(layerItem)
            
            
    def acceptDroppedItem(self, explorer, item):                        
        if isinstance(item, GsLayerItem):
            addDraggedLayerToGroup(explorer, item.element, self)
            return [self]            
            
    def contextMenuActions(self, explorer):
        explorer = explorer
        editLayerGroupAction = QtGui.QAction("Edit...", explorer)
        editLayerGroupAction.triggered.connect(self.editLayerGroup)             
        deleteLayerGroupAction = QtGui.QAction("Delete", explorer)
        deleteLayerGroupAction.triggered.connect(self.deleteLayerGroup)
        return [editLayerGroupAction, deleteLayerGroupAction]
       
    def multipleSelectionContextMenuActions(self, explorer, selected):
        self.explorer = explorer
        deleteSelectedAction = QtGui.QAction("Delete", explorer)
        deleteSelectedAction.triggered.connect(lambda: self.deleteElements(selected))
        return [deleteSelectedAction]
    
    def deleteLayerGroup(self):
        self.deleteElements([self]);
        
    def editLayerGroup(self):
        cat = self.parentCatalog()        
        dlg = LayerGroupDialog(cat, self.element)
        dlg.exec_()
        group = dlg.group
        if group is not None:
            self.explorer.run(cat.save, "Layer group '" + self.element.name + "' correctly edited", 
                              [self], 
                              group)   
    
                
            

class GsStyleItem(GsTreeItem): 
    def __init__(self, style, isDefault): 
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/style.png")
        name = style.name if not isDefault else style.name + " [default style]"
        GsTreeItem.__init__(self, style, icon, name)
        self.isDefault = isDefault     
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDragEnabled)        
        
    def contextMenuActions(self, explorer):
        self.explorer = explorer    
        actions = []
        if isinstance(self.parent(), GsLayerItem):
            setAsDefaultStyleAction = QtGui.QAction("Set as default style", explorer)
            setAsDefaultStyleAction.triggered.connect(self.setAsDefaultStyle)
            setAsDefaultStyleAction.setEnabled(not self.isDefault)
            actions.append(setAsDefaultStyleAction)  
            removeStyleFromLayerAction = QtGui.QAction("Remove style from layer", explorer)
            removeStyleFromLayerAction.triggered.connect(self.removeStyleFromLayer)
            removeStyleFromLayerAction.setEnabled(not self.isDefault)            
            actions.append(removeStyleFromLayerAction)                           
        else:                      
            deleteStyleAction = QtGui.QAction("Delete", explorer)
            deleteStyleAction.triggered.connect(self.deleteStyle)
            actions.append(deleteStyleAction)
        return actions 
    
    
    def acceptDroppedItem(self, explorer, item): 
        if isinstance(item, (GsStyleItem, QgsStyleItem)):  
            if isinstance(self.parent(), GsLayerItem):
                destinationItem = self.parent()
                addDraggedStyleToLayer(explorer, item, destinationItem)
                return [destinationItem]
            elif isinstance(self.parent(), GsStylesItem) and isinstance(item, QgsStyleItem):
                destinationItem = self.parent()
                publishDraggedStyle(explorer, item.element.name(), destinationItem)
                return [destinationItem]              
    
    def multipleSelectionContextMenuActions(self, explorer, selected):
        self.explorer = explorer
        deleteSelectedAction = QtGui.QAction("Delete", explorer)
        deleteSelectedAction.triggered.connect(lambda: self.deleteElements(selected))
        return [deleteSelectedAction]
    
    def deleteStyle(self):
        self.deleteElements([self])
        
    def removeStyleFromLayer(self):
        layer = self.parent().element        
        styles = layer.styles
        styles = [style for style in styles if style.name != self.element.name]            
        layer.styles = styles 
        self.explorer.run(self.parentCatalog().save, 
                "Style '" + self.element.name + "' removed from layer '" + layer.name, 
                self.explorer.tree.findAllItems(self.parent().element),
                layer)
    
    def setAsDefaultStyle(self):
        layer = self.parent().element        
        styles = layer.styles
        styles = [style for style in styles if style.name != self.element.name]
        default = layer.default_style
        if default is not None:
            styles.append(default)
        layer.default_style = self.element
        layer.styles = styles 
        self.explorer.run(self.parentCatalog().save, 
                "Style '" + self.element.name + "' set as default style for layer '" + layer.name + "'", 
                self.explorer.tree.findAllItems(self.parent().element),
                layer)          
    
                      
class GsWorkspaceItem(GsTreeItem): 
    def __init__(self, workspace, isDefault):
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/workspace.png")                 
        self.isDefault = isDefault        
        name = workspace.name if not isDefault else workspace.name + " [default workspace]"
        GsTreeItem.__init__(self, workspace, icon, name)    
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDropEnabled)  
        
    def populate(self):
        stores = self.element.catalog.get_stores(self.element)
        for store in stores:
            storeItem = GsStoreItem(store)
            storeItem.populate()
            self.addChild(storeItem)         
                   
    def acceptDroppedItem(self, explorer, item):                        
        if isinstance(item, QgsGroupItem):                
            catalog = self.parentCatalog()
            if catalog is None:
                return
            workspace = self.parentWorkspace()
            if workspace is None:
                workspace = self.getDefaultWorkspace()
            publishDraggedGroup(explorer, item, catalog, workspace)
            return explorer.tree.findAllItems(catalog) 
        elif isinstance(item, QgsLayerItem):
            publishDraggedLayer(explorer, item.element, self.element)
            return explorer.tree.findAllItems(self.element.catalog)
        elif isinstance(item, PgTableItem):
            catalog = self.parentCatalog()
            workspace = self.element
            toUpdate = []
            if workspace is not None:
                publishDraggedTable(explorer, item.element, workspace)
                toUpdate.append(explorer.tree.findAllItems(catalog)[0])  
            return toUpdate        
                                    
                                     
    def contextMenuActions(self, explorer):
        self.explorer = explorer
        setAsDefaultAction = QtGui.QAction("Set as default workspace", explorer)
        setAsDefaultAction.triggered.connect(self.setAsDefaultWorkspace)
        setAsDefaultAction.setEnabled(not self.isDefault)                                
        deleteWorkspaceAction = QtGui.QAction("Delete", explorer)
        deleteWorkspaceAction.triggered.connect(self.deleteWorkspace)
        return[setAsDefaultAction, deleteWorkspaceAction]
        
    def multipleSelectionContextMenuActions(self, explorer, selected):
        self.explorer = explorer
        deleteSelectedAction = QtGui.QAction("Delete", explorer)
        deleteSelectedAction.triggered.connect(lambda: self.deleteElements(selected))
        return [deleteSelectedAction]
    
    def deleteWorkspace(self):
        self.deleteElements([self])
        
    def setAsDefaultWorkspace(self):
        self.explorer.run(self.parentCatalog().set_default_workspace, 
                 "Workspace '" + self.element.name + "' set as default workspace",
                 [self.parent()],
                 self.element.name)
        
                                     
class GsStoreItem(GsTreeItem): 
    def __init__(self, store):
        if isinstance(store, DataStore):
            icon = None#QtGui.QIcon(os.path.dirname(__file__) + "/../images/workspace.png")
        else:
            icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/grid.jpg")             
        GsTreeItem.__init__(self, store, icon)
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDropEnabled)  

    def populate(self):      
        resources = self.element.get_resources()
        for resource in resources:
            resourceItem = GsResourceItem(resource)                        
            self.addChild(resourceItem)        

    def acceptDroppedItem(self, explorer, item):  
        if isinstance(item, QgsLayerItem):      
            publishDraggedLayer(explorer, item.element, self.element.workspace)
            return explorer.tree.findAllItems(self.element.catalog)        
    
    def contextMenuActions(self, explorer):
        self.explorer = explorer
        deleteStoreAction = QtGui.QAction("Delete", explorer)
        deleteStoreAction.triggered.connect(self.deleteStore)
        return[deleteStoreAction]
                
    def multipleSelectionContextMenuActions(self, explorer, selected):
        self.explorer = explorer
        deleteSelectedAction = QtGui.QAction("Delete", explorer)
        deleteSelectedAction.triggered.connect(lambda: self.deleteElements(selected))
        return [deleteSelectedAction]
                    
    def deleteStore(self):
        self.deleteElements([self])
        
class GsResourceItem(GsTreeItem): 
    def __init__(self, resource):  
        if isinstance(resource, Coverage):
            icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/grid.jpg")
        else:
            icon = None#QtGui.QIcon(os.path.dirname(__file__) + "/../images/workspace.png")
        GsTreeItem.__init__(self, resource, icon)
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDropEnabled)  

    def acceptDroppedItem(self, explorer, item):  
        if isinstance(item, QgsLayerItem):      
            publishDraggedLayer(explorer, item.element, self.element.workspace)
            return explorer.tree.findAllItems(self.element.catalog)
    
    def contextMenuActions(self, explorer):
        self.explorer = explorer
        deleteResourceAction = QtGui.QAction("Delete", explorer)
        deleteResourceAction.triggered.connect(self.deleteResource)
        return[deleteResourceAction]
                
    def deleteResource(self):
        self.deleteElements([self])      

#### GWC ####

class GwcLayersItem(GsTreeItem): 
    def __init__(self):
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/gwc.png")
        GsTreeItem.__init__(self, None, icon, "GeoWebCache layers")                                    
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDropEnabled)

    def populate(self):
        catalog = self.parentCatalog()
        self.element = Gwc(catalog)        
        layers = self.element.layers()
        for layer in layers:
            item = GwcLayerItem(layer)
            self.addChild(item)

    def acceptDroppedItem(self, explorer, item):  
        if isinstance(item, GsLayerItem):      
            createGwcLayer(explorer, item.element)
            return [self]
        return []
    
    def contextMenuActions(self, explorer):
        self.explorer = explorer   
        addGwcLayerAction = QtGui.QAction("New GWC layer...", explorer)
        addGwcLayerAction.triggered.connect(self.addGwcLayer)
        return [addGwcLayerAction]        
               
     
    def addGwcLayer(self):
        cat = self.parentCatalog()
        layers = cat.get_layers()              
        dlg = EditGwcLayerDialog(layers, None)
        dlg.exec_()        
        if dlg.gridsets is not None:
            layer = dlg.layer
            gwc = Gwc(layer.catalog)
            
            #TODO: this is a hack that assumes the layer belong to the same workspace
            typename = layer.resource.workspace.name + ":" + layer.name
            
            gwclayer= GwcLayer(gwc, typename, dlg.formats, dlg.gridsets, dlg.metaWidth, dlg.metaHeight)
            catItem = self.explorer.tree.findAllItems(cat)            
            self.explorer.run(gwc.addLayer,
                              "GWC layer '" + layer.name + "' correctly created",
                              [catItem.gwcItem],
                              gwclayer)             
                            

          
                
class GwcLayerItem(GsTreeItem): 
    def __init__(self, layer):          
        icon = QtGui.QIcon(os.path.dirname(__file__) + "/../images/layer.png")        
        GsTreeItem.__init__(self, layer, icon)
        self.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDropEnabled)
        
    def contextMenuActions(self, explorer):
        self.explorer = explorer  
        editGwcLayerAction = QtGui.QAction("Edit...", explorer)
        editGwcLayerAction.triggered.connect(self.editGwcLayer)           
        seedGwcLayerAction = QtGui.QAction("Seed...", explorer)
        seedGwcLayerAction.triggered.connect(self.seedGwcLayer)        
        emptyGwcLayerAction = QtGui.QAction("Empty", explorer)
        emptyGwcLayerAction.triggered.connect(self.emptyGwcLayer)                  
        deleteLayerAction = QtGui.QAction("Delete", explorer)
        deleteLayerAction.triggered.connect(self.deleteLayer)
        return[editGwcLayerAction, seedGwcLayerAction, emptyGwcLayerAction, deleteLayerAction]

    def multipleSelectionContextMenuActions(self, explorer, selected):
        self.explorer = explorer
        deleteSelectedAction = QtGui.QAction("Delete", explorer)
        deleteSelectedAction.triggered.connect(lambda: self.deleteElements(selected))
        return [deleteSelectedAction]
    
    def descriptionWidget(self):
        
        text = self.getSeedingTasksStateDescription()
        self.webView = QtWebKit.QWebView()
        self.webView.page().setLinkDelegationPolicy(QtWebKit.QWebPage.DelegateAllLinks)
        self.webView.connect(self.webView, SIGNAL("linkClicked(const QUrl&)"), self.linkClicked)
        self.webView.setHtml(text)   
        return self.webView 
    
    def getSeedingTasksStateDescription(self):        
        try:
            state = self.element.getSeedingState()
            if state is None:
                text = "No seeding tasks exist for this layer"
            else:
                text = "This layer is being seeded. Processed {} tiles of {}".format(state[0], state[1])
                text += '</br></br><a href="update">update</a> - <a href="kill">kill</a>'
        except SeedingStatusParsingError:
            text = 'Cannot determine running seeding tasks for this layer'
        return text
    
    def linkClicked(self, url):
        print url.toString()
        if url.toString() == 'kill':
            try:
                self.element.killSeedingTasks()
            except FailedRequestError:
                #TODO:
                return
        text = self.getSeedingTasksStateDescription()
        self.webView.setHtml(text)
        
          
    def deleteLayer(self):
        self.deleteElements([self])      
        
        
    def emptyGwcLayer(self):
        layer = self.element   
        #TODO: confirmation dialog??    
        self.explorer.run(layer.truncate,
                          "GWC layer '" + layer.name + "' correctly truncated",
                          [],
                          )            
    def seedGwcLayer(self):
        layer = self.element   
        dlg = SeedGwcLayerDialog(layer)
        dlg.show()
        dlg.exec_()
        if dlg.format is not None:
            self.explorer.run(layer.seed,
                              "GWC layer '" + layer.name + "' correctly seeded",
                              [],
                              dlg.operation, dlg.format, dlg.gridset, dlg.minzoom, dlg.maxzoom, dlg.extent)
    
    def editGwcLayer(self):
        layer = self.element   
        dlg = EditGwcLayerDialog([layer], layer)
        dlg.exec_()
        if dlg.gridsets is not None:
            self.explorer.run(layer.update,
                              "GWC layer '" + layer.name + "' correctly updated",
                              [],
                              dlg.formats, dlg.gridsets, dlg.metaWidth, dlg.metaHeight)
            
            
            
def publishDraggedGroup(self, groupItem, catalog, workspace):        
    groupName = groupItem.element
    groups = qgislayers.getGroups()   
    group = groups[groupName]           
    gslayers= [layer.name for layer in catalog.get_layers()]
    missing = []         
    for layer in group:            
        if layer.name() not in gslayers:
            missing.append(layer)         
    if missing:
        self.explorer.progress.setMaximum(len(missing))
        progress = 0
        ogcat = OGCatalog(catalog)                  
        for layer in missing:
            self.explorer.progress.setValue(progress)                                           
            self.explorer.run(ogcat.publishLayer,
                     "Layer correctly published from layer '" + layer.name() + "'",
                     [],
                     layer, workspace, True)
            progress += 1                                                            
        self.explorer.progress.setValue(progress)  
    names = [layer.name() for layer in group]      
    layergroup = catalog.create_layergroup(groupName, names, names)
    self.explorer.run(catalog.save, "Layer group correctly created from group '" + groupName + "'", 
             [], layergroup)       

def publishDraggedLayer(explorer, layer, workspace):
    cat = workspace.catalog  
    ogcat = OGCatalog(cat)                                
    explorer.run(ogcat.publishLayer,
             "Layer correctly published from layer '" + layer.name() + "'",
             [],
             layer, workspace, True)
    
def publishDraggedTable(explorer, table, workspace):    
    cat = workspace.catalog                          
    explorer.run(_publishTable,
             "Table correctly published from table '" + table.name + "'",
             [],
             table, cat, workspace)
    
            
def _publishTable(table, catalog = None, workspace = None):
    if catalog is None:
        pass       
    workspace = workspace if workspace is not None else catalog.get_default_workspace()
    connection = table.conn   
    geodb = connection.geodb     
    catalog.create_pg_featurestore(connection.name,                                           
                                   workspace = workspace,
                                   overwrite = True,
                                   host = geodb.host,
                                   database = geodb.dbname,
                                   schema = table.schema,
                                   port = geodb.port,
                                   user = geodb.user,
                                   passwd = geodb.passwd)
    catalog.create_pg_featuretype(table.name, connection.name, workspace)  

def publishDraggedStyle(explorer, layerName, catalogItem):
    ogcat = OGCatalog(catalogItem.element)
    toUpdate = []
    for idx in range(catalogItem.childCount()):
        subitem = catalogItem.child(idx)
        if isinstance(subitem, GsStylesItem):
            toUpdate.append(subitem)
            break                
    explorer.run(ogcat.publishStyle,
             "Style correctly published from layer '" + layerName + "'",
             toUpdate,
             layerName, True, layerName)

def addDraggedLayerToGroup(explorer, layer, groupItem):    
    group = groupItem.element
    styles = group.styles
    layers = group.layers
    if layer.name not in layers:
        layers.append(layer.name)
        styles.append(layer.default_style.name)
    group.dirty.update(layers = layers, styles = styles)
    explorer.run(layer.catalog.save,
                 "Group '" + group.name + "' correctly updated",
                 [groupItem],
                 group)
    
def addDraggedStyleToLayer(explorer, styleItem, layerItem):
    catalog = layerItem.element.catalog  
    if isinstance(styleItem, QgsStyleItem):
        styleName = styleItem.element.name()                   
        catalogItem = explorer.tree.findAllItems(catalog)[0]
        publishDraggedStyle(explorer, styleName, catalogItem)     
        style = catalog.get_style(styleName)
    else:         
        style = styleItem.element            
    layer = layerItem.element
    styles = layer.styles                            
    styles.append(style)
    layer.styles = styles                        
    explorer.run(catalog.save, 
             "Style '" + style.name + "' correctly added to layer '" + layer.name + "'",
             [layerItem],
             layer)  
         
        
def createGwcLayer(explorer, layer):                
    dlg = EditGwcLayerDialog([layer], None)
    dlg.exec_()        
    if dlg.gridsets is not None:
        gwc = Gwc(layer.catalog)
        
        #TODO: this is a hack that assumes the layer belong to the same workspace
        typename = layer.resource.workspace.name + ":" + layer.name
        
        gwclayer= GwcLayer(gwc, typename, dlg.formats, dlg.gridsets, dlg.metaWidth, dlg.metaHeight)
        explorer.run(gwc.addLayer,
                          "GWC layer '" + layer.name + "' correctly created",
                          [],
                          gwclayer)                   