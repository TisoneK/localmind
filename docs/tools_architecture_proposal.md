# Tools Architecture Proposal

## Executive Summary

Transform LocalMind's tools from primitive functions into a **constrained execution engine** with modular capabilities, proper abstraction layers, and strict orchestration control.

## Current State Issues

### 1. Tool Inefficiency Example
```python
# Current inefficient approach for "Create todo.txt with 'buy milk' and 'call mom'"
if not os.path.exists('todo.txt'):
    open('todo.txt', 'w').close()
with open('todo.txt', 'a') as f:
    f.write('\nbuy milk\n')
    f.write('call mom\n')
os.system('cat todo.txt')
```

**Problems:**
- Over-execution for trivial tasks
- No semantic intent mapping
- Tool leakage into reasoning
- Unnecessary shell usage

### 2. Architectural Limitations
- Single-file tools limit scalability
- No tool-to-tool communication framework
- Missing execution policies and constraints
- Poor error handling and observability

## Proposed Architecture

### Core Design Principles

1. **Capability/Primitive Separation**
   - `capabilities/` = domain logic (what the tool means)
   - `primitives/` = execution (what the tool does)

2. **Constrained Execution Engine**
   - Intent-to-action mapping
   - Policy enforcement before execution
   - Strict tool lifecycle contracts

3. **Modular Package Structure**
   - Logical domain grouping
   - Appropriate nesting depth (2-3 levels max)
   - Clean interfaces via `__init__.py`

### Architecture Overview

```
tools/
|
|--- base/                          # Tool runtime kernel
|    |--- __init__.py
|    |--- interfaces/               # Core abstractions
|    |    |--- __init__.py
|    |    |--- tool/                # BaseTool + contracts
|    |    |--- registry/            # Tool registry interfaces
|    |    |--- communication/       # Tool-to-tool messaging
|    |--- policies/                 # Execution policies
|    |    |--- __init__.py
|    |    |--- execution/           # Policy engine + rules
|    |    |--- security/            # Security policies
|    |--- observability/            # Metrics + monitoring
|         |--- __init__.py
|         |--- metrics/             # Collection + aggregation
|         |--- audit/               # Audit trails
|
|--- core/                          # NEW: Execution engine
|    |--- execution_engine.py       # Central orchestration
|    |--- intent_mapper.py          # Intent -> Action mapping
|    |--- policy_enforcer.py        # Pre-execution validation
|    |--- tool_lifecycle.py         # Strict contract enforcement
|
|--- file_operations/              # Domain capability
|    |--- __init__.py               # Exports: FileTool
|    |--- main/                     # Public API + orchestration
|    |    |--- __init__.py
|    |    |--- orchestrator.py
|    |    |--- dispatcher.py
|    |--- capabilities/             # Domain logic
|    |    |--- __init__.py
|    |    |--- reader/
|    |    |    |--- __init__.py
|    |    |    |--- parsers.py      # All parsers in one file
|    |    |    |--- chunkers.py
|    |    |    |--- detector.py
|    |    |--- writer/
|    |    |    |--- __init__.py
|    |    |    |--- formatters.py   # All formatters in one file
|    |    |    |--- validators.py
|    |    |    |--- serializers.py
|    |    |--- analyzer/
|    |    |    |--- __init__.py
|    |    |    |--- metadata.py
|    |    |    |--- type_detector.py
|    |    |    |--- integrity.py
|    |--- primitives/               # Raw operations
|    |    |--- __init__.py
|    |    |--- read.py              # File reading operations
|    |    |--- write.py             # File writing operations
|    |    |--- append.py            # File append operations
|    |    |--- delete.py            # File deletion operations
|    |--- utils/                    # Shared utilities
|         |--- __init__.py
|         |--- path_utils.py
|         |--- permissions.py
|         |--- validation.py
|         |--- error_handling.py
|
|--- code_execution/                # Domain capability
|    |--- __init__.py               # Exports: CodeTool
|    |--- main/                     # Public API + orchestration
|    |--- capabilities/             # Domain logic
|    |    |--- __init__.py
|    |    |--- detector/            # Language detection
|    |    |    |--- __init__.py
|    |    |    |--- language.py
|    |    |    |--- patterns.py
|    |    |--- sandbox/             # Execution environments
|    |    |    |--- __init__.py
|    |    |    |--- python.py        # Python sandbox
|    |    |    |--- javascript.py    # JavaScript sandbox
|    |    |--- analyzer/            # Code analysis
|    |    |    |--- __init__.py
|    |    |    |--- security_scanner.py
|    |    |    |--- complexity.py
|    |--- primitives/               # Raw operations
|    |    |--- __init__.py
|    |    |--- process.py           # Process management
|    |    |--- subprocess.py        # Subprocess execution
|    |    |--- isolation.py         # Isolation mechanisms
|    |--- utils/                    # Shared utilities
|         |--- __init__.py
|         |--- code_extraction.py
|         |--- output_formatting.py
|         |--- error_handling.py
|
|--- system/                        # Domain capability
|    |--- __init__.py               # Exports: SystemTool
|    |--- main/                     # Public API + orchestration
|    |--- capabilities/             # Domain logic
|    |    |--- __init__.py
|    |    |--- info/                # System information
|    |    |    |--- __init__.py
|    |    |    |--- hardware.py
|    |    |    |--- software.py
|    |    |    |--- network.py
|    |    |--- shell/               # Shell operations
|    |    |    |--- __init__.py
|    |    |    |--- executor.py
|    |    |    |--- parser.py
|    |    |    |--- security.py
|    |    |--- monitoring/          # Resource monitoring
|    |    |    |--- __init__.py
|    |    |    |--- resources.py
|    |    |    |--- processes.py
|    |    |    |--- performance.py
|    |--- primitives/               # Raw operations
|    |    |--- __init__.py
|    |    |--- commands.py          # Command execution
|    |    |--- process.py           # Process management
|    |    |--- system_calls.py      # System call interface
|
|--- web/                           # Domain capability
|    |--- __init__.py               # Exports: WebTool
|    |--- main/                     # Public API + orchestration
|    |--- capabilities/             # Domain logic
|    |    |--- __init__.py
|    |    |--- search/              # Web search
|    |    |    |--- __init__.py
|    |    |    |--- engines.py
|    |    |    |--- parsers.py
|    |    |--- scraper/             # Web scraping
|    |    |    |--- __init__.py
|    |    |    |--- extractor.py
|    |    |    |--- parser.py
|    |    |--- api_client/          # API communication
|    |    |    |--- __init__.py
|    |    |    |--- http_client.py
|    |    |    |--- auth.py
|    |--- primitives/               # Raw operations
|    |    |--- __init__.py
|    |    |--- http.py              # HTTP requests
|    |    |--- dns.py               # DNS resolution
|    |    |--- socket.py            # Socket operations
|    |--- utils/                    # Shared utilities
|         |--- __init__.py
|         |--- url_utils.py
|         |--- html_parser.py
|         |--- error_handling.py
```

## Key Components

### 1. Execution Engine (NEW)

```python
# core/execution_engine.py
class ExecutionEngine:
    """
    Central orchestration point for all tool execution.
    Enforces policies, manages lifecycle, and ensures consistency.
    """
    
    async def execute(self, intent: str, payload: dict, context: dict) -> ToolResult:
        """
        Strict execution flow:
        1. Intent mapping
        2. Policy validation
        3. Tool selection
        4. Execution
        5. Observability
        """
        pass
```

### 2. Intent Mapper (NEW)

```python
# core/intent_mapper.py
class IntentMapper:
    """
    Maps high-level intents to specific tool capabilities.
    Reduces LLM confusion and tool misuse.
    """
    
    INTENT_MAP = {
        "create_file": "FileTool.write",
        "read_file": "FileTool.read",
        "execute_code": "CodeTool.execute",
        "search_web": "WebTool.search",
        "system_info": "SystemTool.info"
    }
    
    def map_intent(self, user_intent: str) -> Tuple[str, str]:
        """Map user intent to tool and action"""
        pass
```

### 3. Policy Enforcer (NEW)

```python
# core/policy_enforcer.py
class PolicyEnforcer:
    """
    Pre-execution policy validation.
    Runs BEFORE tool execution, not inside tools.
    """
    
    POLICIES = {
        "file_operations": "prefer_native_over_shell",
        "code_execution": "sandbox_required",
        "web_access": "rate_limit_enforced"
    }
    
    async def validate(self, tool: str, action: str, payload: dict) -> bool:
        """Validate against all policies"""
        pass
```

### 4. Tool Lifecycle Contract (STRICT)

```python
# base/interfaces/tool/__init__.py
class BaseTool(ABC):
    """
    Strict contract that all tools MUST implement.
    No random inputs - structured execution only.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool identifier"""
        pass
    
    @property
    @abstractmethod
    def intents(self) -> List[str]:
        """Supported intents"""
        pass
    
    @property
    @abstractmethod
    def dependencies(self) -> List[str]:
        """Other tools this can call"""
        pass
    
    @abstractmethod
    async def execute(self, intent: str, payload: dict, context: dict) -> ToolResult:
        """
        STRICT CONTRACT - No variations allowed
        intent: High-level intent (e.g., "create_todo_file")
        payload: Structured data
        context: Execution context
        """
        pass
```

### 5. Tool Bus (Controlled Communication)

```python
# base/interfaces/communication/__init__.py
class ToolBus:
    """
    Safe tool-to-tool communication with constraints.
    Prevents recursion bombs and circular dependencies.
    """
    
    MAX_DEPTH = 3
    ALLOWED_CYCLES = False
    
    async def call_tool(self, caller: str, target: str, intent: str, payload: dict) -> ToolResult:
        """Safe tool-to-tool communication with validation"""
        pass
```

## Execution Flow

```
Agent Request ("Create todo.txt with items")
  |
  v
IntentMapper.map("create_todo_file") 
  |
  v
PolicyEnforcer.validate(FileTool.write)
  |
  v
ExecutionEngine.execute()
  |
  v
FileTool.execute(intent="create_todo_file", payload={...})
  |
  v
Capability Layer (file_operations/main/orchestrator.py)
  |
  v
Primitive Layer (file_operations/primitives/write.py)
  |
  v
Observability.hooks()
  |
  v
ToolResult (structured response)
```

## Benefits

### 1. Efficiency
- **Before**: 5 separate operations for simple file creation
- **After**: 1 intent-driven operation with optimized execution

### 2. Reliability
- Strict contracts prevent inconsistent behavior
- Policy enforcement prevents dangerous operations
- Controlled tool communication prevents recursion

### 3. Scalability
- Modular structure allows independent development
- Clear separation of concerns
- Proper abstraction layers

### 4. Maintainability
- Organized package structure
- Clean interfaces
- Comprehensive observability

## Migration Strategy

### Phase 1: Foundation (Week 1)
1. Create `base/` package with interfaces and policies
2. Implement `core/` execution engine
3. Define strict tool contracts

### Phase 2: Refactor File Operations (Week 2)
1. Convert `file_reader.py` and `file_writer.py` to modular structure
2. Implement intent mapping for file operations
3. Add policy enforcement

### Phase 3: Refactor Code Execution (Week 3)
1. Enhance existing `code_executor/` structure
2. Add sandbox policies and security
3. Implement tool communication

### Phase 4: System and Web Tools (Week 4)
1. Modularize system tools
2. Implement web capabilities
3. Add cross-tool communication

### Phase 5: Integration and Testing (Week 5)
1. Integration testing across all tools
2. Performance optimization
3. Documentation and training

## Success Metrics

1. **Performance**: 50% reduction in tool execution steps
2. **Reliability**: 90% reduction in tool-related errors
3. **Developer Experience**: 70% faster tool development
4. **Maintainability**: 60% reduction in code duplication

## Conclusion

This architecture transforms LocalMind from a collection of tool functions into a sophisticated execution engine. The key innovations are:

1. **Intent-driven execution** instead of low-level operations
2. **Policy enforcement** before execution
3. **Strict contracts** for consistency
4. **Modular capabilities** for scalability
5. **Controlled communication** for reliability

The result is a production-grade tool system that can scale efficiently while maintaining reliability and developer productivity.
