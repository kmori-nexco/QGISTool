from .plugin import QGISToolPlugin

def classFactory(iface):
  return QGISToolPlugin(iface)
