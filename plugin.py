# plugin.py
import os

try:
    # PyQt6 / Qt6 系 (QGIS 3.30+)
    from qgis.PyQt.QtGui import QAction, QIcon
except ImportError:
    # PyQt5 / Qt5 系 (QGIS 3.16〜3.28)
    from qgis.PyQt.QtWidgets import QAction
    from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt

from .utils import settings, SKEY_GEOM
from .viewer import PhotoViewerPlus

class QGISToolPlugin:
    def __init__(self, iface_):
        self.iface = iface_
        self.action = None
        self.viewer = None

    def initGui(self):
        plugin_dir = os.path.dirname(__file__)
        icon_path = os.path.join(plugin_dir, "icon.png")
        
        # アクション作成
        self.action = QAction(QIcon('icon.png'), 'QGISTool', self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        # メニュー＆ツールバーに追加
        self.iface.addPluginToMenu('&QGISTool', self.action)
        self.iface.addToolBarIcon(self.action)

        # （開発中の自動起動は残す／外すはお好み）
        self.run()

    def unload(self):
        # メニュー＆ツールバーから削除
        if self.action:
            self.iface.removePluginMenu('&QGISTool', self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        
        # ドックと終了処理（ツール解除・ジオメトリ保存）
        if self.viewer and getattr(self.viewer, "dock", None):
            # ジオメトリ保存
            try:
                settings.setValue(SKEY_GEOM, self.viewer.dock.saveGeometry())
            except Exception:
                pass
            # 独自ツール解除（Add/Delete）
            try:
                if hasattr(self.viewer, "force_disable_map_tools"):
                    self.viewer.force_disable_map_tools()
            except Exception:
                pass
            # Dock を QGIS から外して破棄
            try:
                self.iface.removeDockWidget(self.viewer.dock)
                self.viewer.dock.deleteLater()
            except Exception:
                pass
        self.viewer = None

    def run(self):
        # 既にあれば再表示
        if self.viewer and getattr(self.viewer, "dock", None):
            self.viewer.dock.show()
            self.viewer.dock.raise_()
            return

        # 初回起動 → 生成
        self.viewer = PhotoViewerPlus()
        if getattr(self.viewer, "dock", None):
            try:
                if self.viewer.dock.parent() is None:
                    self.iface.addDockWidget(Qt.RightDockWidgetArea, self.viewer.dock)
            except Exception:
                pass
            # ジオメトリ復元
            try:
                geom = settings.value(SKEY_GEOM, None)
                if geom:
                    self.viewer.dock.restoreGeometry(geom)
            except Exception:
                pass
            self.viewer.dock.show()
            self.viewer.dock.raise_()