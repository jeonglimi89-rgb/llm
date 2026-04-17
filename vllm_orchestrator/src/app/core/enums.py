"""공통 enum"""
from enum import Enum


class TaskDomain(str, Enum):
    BUILDER = "builder"
    CAD = "cad"
    MINECRAFT = "minecraft"
    ANIMATION = "animation"
    PRODUCT_DESIGN = "product_design"
    NPC = "npc"
    RESOURCEPACK = "resourcepack"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    TIMEOUT = "timeout"
    SHED = "shed"           # load shed로 드랍됨
    DEGRADED = "degraded"   # fallback 모드로 완료


class FallbackMode(str, Enum):
    FULL = "full"
    SHORT = "short"         # 프롬프트 축소
    CACHED = "cached"       # 캐시 응답
    MOCK = "mock"           # 더미 응답
    REJECT = "reject"       # 거부


class OutputType(str, Enum):
    """Allowed output types from the orchestrator."""
    EXECUTABLE_COMMAND_GRAPH = "executable_command_graph"
    EXECUTABLE_COMMAND_GRAPH_WITH_VARIANTS = "executable_command_graph_with_variants"
    CLARIFICATION_REQUIRED = "clarification_required"
    FAIL_LOUD_WITH_REASONS = "fail_loud_with_reasons"
