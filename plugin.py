from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from .viewer import PhotoViewerPlus


class QGISToolPlugin:
  def __init__(self, qgis_iface):
    self.iface = qgis_iface
    self.action = None
    self.viewer = None

  def initGui(self):
    self.action = QAction(QIcon('icon.png'), 'QGISTool', self.iface.mainWindow())
    self.action.triggered.connect(self.run)
    self.iface.addPluginToMenu('&QGISTool', self.action)
    self.iface.addToolBarIcon(self.action)

  def unload(self):
    if self.action:
      self.iface.removePluginMenu('&QGISTool', self.action)
      self.iface.removeToolBarIcon(self.action)
      self.action = None
    if self.viewer and getattr(self.viewer, 'dock', None):
      try:
        self.viewer.dock.close()
      except Exception: pass
    self.viewer = None
  
  def run(self):
    if self.viewer and getattr(self.viewer, 'dock', None):
      self.viewer.dock.raise_()
      self.viewer.dock.show()
      return
    self.viewer = PhotoViewerPlus()
