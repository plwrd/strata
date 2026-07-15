"""WebChannel registration.

The frontend can only reach objects registered here, and on those objects only
the methods marked ``@Slot``. Adding a bridge object is therefore a deliberate,
reviewable widening of the attack surface — which is the point.
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWebChannel import QWebChannel

from app.bridge.ai_bridge import AIComposerBridge
from app.bridge.collaboration_bridge import CollaborationBridge
from app.bridge.export_bridge import ExportBridge
from app.bridge.graph_bridge import GraphBridge
from app.bridge.job_bridge import JobBridge
from app.bridge.layer_bridge import LayerBridge
from app.bridge.notes_bridge import NotesBridge
from app.bridge.operations_bridge import OperationsBridge
from app.bridge.search_bridge import SearchBridge
from app.bridge.settings_bridge import SettingsBridge
from app.bridge.snapshot_bridge import SnapshotBridge
from app.bridge.views_bridge import ViewsBridge
from app.bridge.workspace_bridge import WorkspaceBridge
from app.services.container import Services

# The names the frontend sees on `channel.objects`.
BRIDGE_NAMES = (
    "workspace",
    "layers",
    "notes",
    "graph",
    "search",
    "ai",
    "export",
    "collaboration",
    "settings",
    "snapshots",
    "operations",
    "views",
    "jobs",
)


def build_channel(services: Services, parent: QObject | None = None) -> QWebChannel:
    channel = QWebChannel(parent)
    bridges: dict[str, QObject] = {
        "workspace": WorkspaceBridge(services, parent),
        "layers": LayerBridge(services, parent),
        "notes": NotesBridge(services, parent),
        "graph": GraphBridge(services, parent),
        "search": SearchBridge(services, parent),
        "ai": AIComposerBridge(services, parent),
        "export": ExportBridge(services, parent),
        "collaboration": CollaborationBridge(services, parent),
        "settings": SettingsBridge(services, parent),
        "snapshots": SnapshotBridge(services, parent),
        "operations": OperationsBridge(services, parent),
        "views": ViewsBridge(services, parent),
        "jobs": JobBridge(services, parent),
    }
    assert set(bridges) == set(BRIDGE_NAMES)
    for name, bridge in bridges.items():
        channel.registerObject(name, bridge)
    return channel
