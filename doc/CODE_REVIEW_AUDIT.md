# Code Review Audit

This audit records the concrete evidence gathered while reviewing the project
for modularity, maintainability, test coverage, and code style.

## Scope

- Runtime/UI application code under `invincat_cli/`.
- Scheduler, server, remote client, hooks, project context, theme preference,
  and terminal lifecycle helpers.
- Unit tests under `tests/unit/`.
- Project quality configuration in `pyproject.toml` and `pytest.ini`.

## Findings Addressed

- Removed duplicate pytest configuration from `pyproject.toml`; `pytest.ini`
  is now the single pytest config source.
- Applied Ruff formatter across the Python codebase so the configured style is
  actually enforced by `ruff format --check`.
- Added focused unit tests for low-risk core paths:
  - package entrypoints and lazy exports
  - JSON CLI output helpers
  - session stats and token formatting
  - debug logging setup
  - MCP trust persistence
  - server config and server manager helpers
  - project context reconstruction
  - app runner and exit cleanup
  - theme preference persistence
  - terminal compatibility helpers
  - hook loading and dispatch
  - remote client message/interrupt conversion
  - link-click URL safety handling in Textual widgets
  - app action handling for interrupt, quit, auto-approve, editor, and tool output flows
  - update command, auto-update, and what's-new handlers
  - startup worker scheduling, git branch detection, skill discovery, and cache prewarm flows
  - pending-message queue processing, pop, and discard behavior
  - schedule tool payloads and schedule manager actions
  - deferred action replacement, drain, error reporting, and plan handoff behavior
  - message mounting, spinner placement, pruning, hydration, and clear behavior
  - memory-update notification, auto-offload, and offload success/error flows
  - chat input routing, queueing, bypass, planner, and thread-switch guards
  - slash-command handlers for clear, tokens, URL, trace, model, and reload flows
  - thread history loading, thread-switch ID application, rollback, and resume failure paths
  - shell command dispatch, execution, cleanup, timeout, and termination behavior
  - skill command discovery, loading, validation, and invocation behavior
  - WeCom bridge command lifecycle, bridge availability, request forwarding, and timeout cleanup
  - server graph construction, tool assembly, project context, MCP, and sandbox setup flows
  - server command/env construction, health checks, subprocess lifecycle, log tail, and restart env flows
  - server startup, resume-thread resolution, ready-state, and failure handlers
  - version lookup error handling
  - clipboard selection/copy fallback behavior
  - external editor command resolution and temp-file readback behavior
  - media file validation, encoding, and multimodal content helpers
  - tool-call and tool-message display formatting
  - Unicode/URL safety helpers
  - update-check cache, configuration, and version comparison flows
  - agent turn start, exception, retry, cleanup, scheduled-run failure, and
    textual execution boundary flows
  - modal UI handlers for theme, language, MCP, memory, thread selection, and
    welcome-banner refresh behavior
  - approval and ask-user interaction handlers, including plan-guard
    auto-reject, deferred placeholders, timeout cleanup, and decision callbacks
  - model selector and switch handlers, including deferred modal actions,
    primary/memory model side effects, default model persistence, and switch
    failure paths
  - scheduled delivery handlers, including timeout cancellation, runner startup,
    WeCom text delivery statuses, missing delivery targets, and queue injection
  - startup handlers, including mount initialization, git fallback, optional
    tool checks, skill discovery failures, and startup worker scheduling
  - message-flow edge cases for stale queued widgets, queued-message mounting,
    pruning failure/in-flight protection, missing containers, and clear behavior
  - memory/offload handlers, including token-count fallback, memory update
    notification skips, offload budget caching, empty state handling,
    serialized message conversion, and generic offload failure paths
  - thread handlers, including remote/local state fallback, history conversion
    and skip paths, thread-link upgrade scheduling, restore behavior, and
    thread-switch blocking reasons
  - plan-mode handlers, including plan entry/reset, planner-send preconditions,
    approval result mapping, current-todo approval, final handoff generation,
    planner cancellation, and handoff retry preservation
  - server startup handlers, including resume-thread fallback, model creation
    failures, MCP preload metadata, gather failures, server-ready scheduling,
    deferred-drain errors, missing banners, and startup-failure cleanup
  - shell handlers, including interactive command failures, background command
    cancellation, cleanup drain errors, process termination early exits,
    process lookup/OS errors, and timeout escalation to kill
  - slash-command dispatch, deferred URL rendering, trace success/error
    boundaries, model selector/usage paths, reload theme failures, and unknown
    command output
  - theme preference parsing and persistence failures, version metadata lookup
    failures, iTerm compatibility guards, and custom theme registration failures
  - skills CLI validation, agent-name dispatch, parser wiring, skill path
    containment, metadata formatting, and template generation behavior
  - session timestamp/path formatting, recent-thread caches, checkpoint summary
    extraction, SQLite thread listing/search/delete behavior, and delete-cache
    invalidation
  - local-context script assembly, MCP prompt formatting, sync/async backend
    detection, summarization refresh handling, and model prompt injection
  - sandbox factory setup-script execution, provider lifecycle cleanup,
    dependency preflight, working-directory mapping, unknown-provider handling,
    known-provider dispatch, and LangSmith template resolution
  - non-interactive input helpers, including media placeholder tracking,
    `@file` mention parsing, pasted-path/file-URL normalization, leading-path
    recovery, strict/simple pasted payload parsing, blank and invalid paste
    rejection, Windows and empty file-URL paths, POSIX/UNC guard paths,
    resolution fallback errors, and Unicode-space filesystem matching
  - skill loading precedence, built-in metadata enrichment, experimental
    Claude skill roots, broken-source tolerance, and skill-content containment
  - configurable-model middleware, including runtime model-parameter merging,
    model override failures, provider-specific setting stripping, Model
    Identity patching, and sync/async wrapper behavior
  - agent tool URL safety, including SSRF guards, DNS resolution failures,
    bounded response reads, redirect handling, request failures, and missing
    Tavily API key behavior
  - approve-plan middleware validation, approval response mapping, prompt
    injection, and sync/async wrapper pass-through behavior
  - i18n translation fallback, formatting failures, tips/language labels,
    global language state, and config-file load/save failure paths
  - media file handling, including image/video size and read failures, video
    signature detection, VideoContentBlock creation, macOS clipboard fallbacks,
    pngpaste invalid/missing/timeout handling, osascript PNG/TIFF success and
    failure paths, temp-file cleanup failures, image format fallback behavior,
    and multimodal content assembly
  - file-operation preview and tracking, including safe reads, memory path
    resolution, write/edit approval diffs, read/write metrics, backend
    download fallback, HITL approval tagging, and parallel tool-call matching
    including ambiguous matches, late tool-call-id updates, path fallback
    failures, backend before/after read exceptions, missing physical paths, and
    empty write no-op metrics
  - MCP config discovery, validation, trust gating, explicit config precedence,
    stdio/remote preflight checks, adapter connection construction, session
    cleanup, and load/init failure wrapping without starting real MCP servers
  - model configuration parsing and persistence, including env-var precedence,
    model-spec validation, provider/profile merges, credential lookup,
    target-specific params, warning suppression, thread selector preferences,
    provider registration failure handling, provider registry fallback
    compatibility, profile-module load failures, default-path persistence,
    and atomic-write temp-file cleanup
  - offload workflow business logic, including retention-limit formatting,
    tool-output trimming, persistent history writes, model creation failure,
    context-limit profile patching, threshold no-op behavior, summary event
    creation, backend fallback, and backend write failure warnings
  - auto-memory injection helpers, including malformed item validation,
    archived/missing store skips, non-dict and invalid-item store rejection,
    hot/warm rendering truncation, total injection budget exhaustion,
    empty-section formatting, async state access failures, and missing-store
    signature fallbacks
  - auto-memory async wrapper edge behavior, including mapping-like state
    objects whose `get()` returns non-dict memory payloads
  - DeepSeek OpenAI-compatible adapter behavior, including reasoning-effort
    stripping, reasoning-content replay, streamed chunk preservation, chat
    result conversion, and sync/async error propagation
  - deterministic integration test model behavior, including metadata/profile
    defaults, no-op tool binding, prompt-derived replies, summary-request
    detection, block-content flattening, empty prompts, and nonstandard content
    stringification
  - scheduled delivery helpers, including WeCom chatid extraction, report path
    confinement, report existence checks, fallback report writes, and webhook
    delivery success/failure handling
  - schedule expression parsing, including invalid time/range errors,
    interval aliases, cron validation failures, weekly/monthly argument
    validation, and human-readable description fallback behavior
  - schedule payload normalization, including invalid schedule-type fallback,
    WeCom delivery target resolution and failure tolerance, report payload
    fields, once-run parse failures, and update recomputation errors
  - scheduled WeCom delivery pure helpers, including missing/non-dict channel
    handling, chat-id lookup, assistant-summary truncation, report-date
    resolution for aware/naive datetimes, error fallback, and text formatting
  - schedule display formatting, including one-shot description hiding,
    missing/unparseable timestamp fallback, naive datetime handling, and
    invalid-timezone UTC fallback
  - schedule management tool middleware, including timezone/once/options
    validation, tool-result parsing, create/update/cancel/delete/run-now
    payloads, scheduled-run tool hiding, and sync/async wrapper rejection
    behavior
  - scheduler store and runner edge cases, including scheduler DB path/open
    failures, PID liveness fallbacks, stale-run detection, scoped/filtered
    store guards, claim rejection, missed one-shot and recurring runs,
    pending-drain skips, injection failure cleanup, and double-finish guards
  - tool approval renderer/widget behavior, including renderer registry
    fallback, write/edit/plan normalization, diff generation/stat counting,
    value and preview truncation, empty-state rendering, and long diff display
  - layout composition helpers, including welcome/chat/status widget wiring and
    custom-theme registration failure tolerance
  - small runtime service helpers, including lazy/default scheduler-store
    construction, default thread-link URL building, and `/tokens` output
    without conversation-breakdown details
  - loading and diff widgets, including spinner frame cycling, mount/update
    timing, pause/resume behavior, diff stats, line numbering, truncation,
    no-change display, and ASCII border handling
  - input history persistence, including JSON/plain-line loading,
    load/write/compact failure tolerance, duplicate and slash-command
    filtering, max-entry compaction, and query-based previous/next navigation
  - link-click and app state helpers, including suspicious-link notification
    failure tolerance, thread-id delegation/reset behavior, and AppResult
    default state
  - memory/offload and theme preference helpers, including auto-offload
    ineligibility, memory-update input normalization/fallbacks, theme save
    temp-file cleanup, and config read/write failure tolerance
  - memory-agent helper behavior, including env parsing fallbacks, target
    language fallback detection, trivial/task-complete turn checks, transcript
    and call-log formatting, operation validation rejection paths,
    missing/corrupt/invalid-schema store handling, dirty item filtering,
    normalization helper fallbacks, next-id handling with malformed items, and
    archived overflow cleanup
  - version/debug helpers, including default SDK version lookup and debug log
    file-open failure reporting
  - UI, queue, and WeCom app-bound handlers, including theme persistence
    exception notification, missing-banner debug logging, empty queue pop,
    inbound WeCom wrapper delegation, and CLI message handoff into WeCom turns
  - schedule app handlers and scheduled-delivery integration, including
    schedule-manager callbacks, active-run completion/skip paths, timeout
    cancellation, WeCom text/report/file delivery success/failure paths, and
    update/auto-update/what's-new error handling
  - server, memory, and shell app-bound handlers, including no-op resume
    intents, MCP preload warnings, empty token-count state, spinner dismissal
    failure logging, process-group termination, timeout SIGKILL escalation,
    and vanished-process wait handling
  - agent, approval, model-argument, and model-switch handlers, including
    invalid approval request filtering, shell allow-list rejection, malformed
    model-params parsing, missing runtime early returns, deferred cleanup
    stops while shell work is active, missing status bars, unverifiable
    credentials, missing server runtime, model metadata failures, and memory
    target model switches
  - planner, startup, and thread-history handlers, including planner-agent
    cache/init/failure paths, Tavily tool gating, planner todo fallback
    extraction and duplicate suppression, git branch lookup failures, optional
    tool-check/prewarm failures, direct checkpointer fallback, thread-link
    upgrade failures, assistant-history render failures, unmatched tool
    messages, unsupported history message types, and resume-summary truncation
  - action shortcuts and message-flow virtualization, including modal/thread
    selector escape routing, chat-input mode exits, shell interruption,
    auto-approve overlay routing, editor no-op paths, spinner repositioning,
    missing input/chat/messages containers, prune no-op and streaming-skip
    paths, stale tool tracking cleanup, hydration batch/sequential fallback,
    widget creation failures, and assistant hydration content failures
  - approval and ask-user orchestration, including shell allow-list
    auto-approval, deferred approval placeholder fallback/timeout/cancel paths,
    plan-guard helper and notice failures, pending-widget wait/timeout paths,
    approval and ask-user mount failures, placeholder removal races, plan
    approval mapper failures, ask-user cleanup failures, and focus restoration
  - plan-mode pure helpers and planner middleware, including latest AI/human
    text extraction for string and block content, Chinese handoff prompt
    selection and i18n fallback, non-list state normalization, latest-turn
    approve/write-todos detection variants, assistant text after tool calls,
    planner visible-tool filtering for dict/object tools, sync/async tool-call
    allow-list wrappers, and numbered todo extraction
  - hook dispatch, approve-plan middleware, and MCP trust persistence,
    including malformed hook config handling, subprocess success/failure and
    timeout paths, fire-and-forget no-loop tolerance, approve-plan interrupt
    request parsing, async prompt append behavior, trust fingerprint read
    failures, config load/save failures, temp-file cleanup, default config
    path selection, revoke no-op behavior, and save-failure return values
  - remote agent client adaptation, including lazy RemoteGraph construction,
    stream conversion for pre-deserialized messages, dropped/unknown message
    handling, interrupt/update pass-through, state lookup 404 fallback,
    state update error logging, thread-registration metadata normalization,
    ensure-thread failures, message-type aliases, tool-call chunk variants,
    parsed tool calls, and message-construction failure paths
  - MCP config resolution edge cases, including project MCP denial paths where
    filtering still returns loadable safe server entries, persisted-trust
    denial with non-empty filtered configs, merged-config validation, explicit
    config precedence, and no-server merge results
  - scheduler parsing and project-context helpers, including bare and prefixed
    cron passthrough, croniter-missing fallback, daily/weekly/monthly success
    and arity failures, interval descriptions, unrecognised schedule errors,
    absolute user-path resolution, server project-root fallback detection, and
    path-resolution failure handling
  - i18n and server subprocess config helpers, including invalid-language
    fallback on Python 3.11 enum membership behavior, translation formatting
    failures, unknown language display names, absent env-var defaults,
    no-context path normalization, and path-resolution error wrapping
  - local-context middleware, including short and truncated MCP tool
    inventory formatting, initial detection failure no-op behavior, async
    initial/refresh detection success and failure paths, existing-context
    skips, and sync fallback exception handling
  - clipboard and external-editor helpers, including OSC52 tmux escape
    writing, missing `pyperclip` fallback to app clipboard, empty selection
    result skips, empty editor command handling, command-not-found handling,
    editor subprocess failure, non-zero exits, empty edits, and line-ending
    normalization
  - tool display and built-in web tools, including timeout coercion/display,
    relative/absolute/unsafe path rendering, hidden Unicode markers, execute
    default/non-default timeout display, task fallback labels, JSON/string
    fallback rendering for non-serializable content, Tavily client caching and
    missing-key behavior, Tavily dependency/client errors, fetch dependency
    errors, SSRF URL guards, redirect handling, and removal of an unreachable
    `fetch_url` no-response branch
  - Unicode and URL safety helpers, including no-host URL results, empty
    warning formatting, punycode decode failures, hostname label splitting,
    local/IP hostname detection edge cases, script classification for
    fullwidth, Greek, Armenian, East Asian, inherited, common, and other
    characters, common/inherited script filtering, and nested-list string
    extraction
  - sandbox provider and server lifecycle orchestration helpers, including
    sandbox error cause exposure, abstract provider default errors, async
    provider wrapper delegation, server env application, temporary workspace
    scaffolding, fake server startup and remote-agent creation, server-start
    failure cleanup, server-session body failure cleanup, and MCP cleanup
    failure logging
  - server graph assembly, including MCP tool load success and FileNotFound /
    RuntimeError propagation, project-context reload behavior, async subagent
    normalization, sandbox context lifetime and atexit cleanup, ImportError,
    NotImplementedError, and generic sandbox creation failure exits, plus
    module-import graph initialization failure reporting
  - app-server process management, including socket port probing, ephemeral
    port selection, health-check timeout diagnostics, invalid checkpointer
    config tolerance, log-tail read failures, temp config directories,
    already-running starts, subprocess timeout/kill handling, cleanup error
    tolerance, owned config-dir removal, restart env rollback, and async
    context-manager entry/exit
  - WeCom message-session helpers, including all progress-line state
    combinations, empty-exception fallback display, stream-content flush
    failures, turn-failure final response generation, and report-error
    suppression
  - WeCom turn-runner lifecycle, including idle/busy timeouts, lock-race
    handling, context enter/exit callbacks, streaming text updates, outbound
    file request success/failure notifications, post-file progress suppression,
    running/completed tool progress observation, paused-stream cursor blinking,
    timeout cancellation, assistant/error/fallback final-answer selection, and
    tool-message de-duplication
  - WeCom protocol pure helpers, including subscribe/stream frame builders,
    request-id extraction, safe-content empty/truncation handling, text/support
    frame rejection, invalid media payloads, mixed-media filtering, voice
    extractor malformed shapes, and agent-input text/mixed/unknown fallbacks
  - WeCom media helpers, including HTTP client construction, AES key/decrypt
    error paths, filename hint/path/image fallbacks, invalid/oversized download
    responses, duplicate download filenames, media-free agent input delegation,
    upload empty/oversized/chunk-size and missing-id failures, and send-file
    payload path confinement checks
  - WeCom file-tool middleware, including disabled-context hiding, unknown tool
    retention, sync/async model and tool wrappers, direct-call rejection,
    WeCom-context pass-through, missing/empty/oversized/outside-root file
    validation, and list/string ToolMessage payload parsing
  - WeCom bridge lifecycle, including allow-list chat/user acceptance and
    rejection, queue caps, outbox success/failure, request matching, offline /
    missing-id / timeout / server-error paths, websocket close fallback,
    subscribe ACK/failure handling, reconnect backoff, cancelled runs,
    stale-progress discard, pending request cancellation, message task cleanup,
    and fake-websocket main-loop frame processing
  - WeCom headless handler, including debounced content emission/cancellation,
    chat-id/session resolution, LRU session eviction, run-turn success/failure
    accounting, AI text extraction, stream filtering, tool-call progress
    detection, HITL auto-resume, file-tool de-duplication/failure tolerance,
    cancellation cleanup, and schedule create/update/cancel/run-now persistence
    success and failure paths
  - WeCom daemon helpers, including env config validation, state/lock PID
    parsing, verified stop fallback, socket RPC success/failure, daemon status
    fallback, owner-only state-file chmod fallback, startup status pipe
    handling, foreground startup/cleanup guards, daemon stdio fd cleanup,
    double-fork parent and already-running guards, fake-dependency daemon ready
    lifecycle, bridge-startup failures, scheduler store filtering, injection
    finish accounting and timeout cancellation, scheduled timeout/result
    delivery retry classes, missing scheduled-task injection, no-target
    scheduled runs, reconcile/start-notice/final-delivery/finish-run failure
    tolerance, timeout no-op/failure paths, and socket client status/stop/error
    responses
  - skills command bodies, including list/create/info/delete text and JSON
    flows, invalid names, missing project roots, empty project text output,
    read failures, metadata/supporting-file display fallbacks, confirmation
    cancellation, dry-run handling, deletion failure handling, parser help
    dispatch, double path validation, symlink rejection, and safe deletion of
    skills discovered under the `.agents/skills` alias directories
  - CLI entrypoint helpers, including dependency/tool warnings, argparse
    subcommand parsing, stdin pipe merging and validation, update command
    success/failure paths, ACP import and flag validation, optional-tool warning
    failure tolerance, MCP metadata preload cleanup, project MCP trust
    decisions, Textual launch kwargs, exit banner/resume-hint failure tolerance,
    ACP server startup/failure cleanup, WeCom foreground daemon startup, and
    UTF-8 locale fallback behavior
  - output formatter helpers, including empty output/prefix handling, unknown
    tool preview truncation, todo parsing and preview limits, ls/search/shell
    formatting, file character truncation, web markdown/generic/result
    formatting, malformed web fallbacks, unknown non-preview fallback, malformed
    embedded todo-list parsing, non-preview file output, search list preview
    file-count truncation, non-dict web literal fallbacks, and task preview
    truncation
  - message-store virtualization helpers, including message/widget
    serialization, required field validation, tool-call lookup and re-keying,
    stale tool-call index removal, append overflow warnings, active-message
    pruning guards, hydration clamping, scroll threshold checks, and clear/range
    accessors
  - message widget helpers, including successful-command trailer stripping,
    mode color fallback logging, timestamp toast fallback/notification paths,
    user and queued-user render prefixes, email-safe mention highlighting,
    skill frontmatter stripping, skill body preview/toggle behavior, diff
    counts and keyboard expansion, and error/app/summarization rendering
  - status bar widget helpers, including display-width right truncation,
    model label sizing and ellipsis rendering, mode/plan/auto-approve pill
    updates, status-message classification, git branch and cwd display updates,
    missing-widget tolerance, message-count rendering, token count
    warning/danger thresholds, memory-model follow-primary behavior, and
    resize-driven visibility
  - status bar widget edge behavior, including full compose output,
    prefix-colored truncated model labels, and complete status widget branch
    coverage
  - welcome banner helpers, including failure/connecting/ready footer
    rendering, editable-install/project/thread/MCP banner composition,
    ANSI-theme and failure states, state-transition rebuilds, theme-change
    refresh, and LangSmith project URL fetch success/error paths
  - memory viewer helpers/actions, including timestamp parsing, text trimming,
    tier and score normalization, active/archive-aware sort modes, atomic item
    deletion and invalid-schema rejection, mount/unmount timer lifecycle,
    refresh/archive/sort/navigation actions, delete confirmation success/error
    paths, empty-scope handling, and cancel dismissal
  - approval menu helpers, including shell command truncation/expansion,
    hidden-Unicode command warnings, empty-request guard rails, option
    selection and shortcut decisions, expandable command toggling, nested
    Unicode and suspicious URL warning collection, minimal and batched compose
    output, warning preview overflow, single/batched option variants,
    completed-future preservation, non-dict and safe URL argument skips,
    decoded-host warning detail, mount-time ASCII border/focus behavior,
    renderer-driven tool-info mounting, and blur refocus behavior
  - autocomplete helpers/controllers, including slash-command scoring and
    selection, fuzzy file scoring/search and gitless project-file fallback,
    `@file` mention application and cache warming, shell command discovery,
    path escaping/unescaping, shell token parsing, home-directory path
    suggestions, tab-cycle completion, enter/reset behavior, and
    multi-controller activation/switching
  - autocomplete widget edge behavior, including slash description
    non-boundary scoring, failed slash completion key handling, fallback file
    cap/glob failures, filename/path fuzzy-score variants, fuzzy file cache
    reuse and warm-cache no-ops, dotfile query inclusion, unknown key handling,
    shell PATH skip/error paths, double-quoted/single-quoted token parsing,
    longest-common-prefix exact matches, `~` path expansion, shell cache
    refresh/warm failures, no-suggestion tab handling, and directory/file
    completion replacement variants
  - chat input low-risk widget helpers, including completion-option rendering
    and click messages, popup selection/hide/show state, completion coordinate
    mapping, mode-prefix stripping, submit prefix/history/reset behavior,
    media-sync skip guards, value/focus/disabled helpers, completion render and
    clear paths, popup click replacement, adapter index translation, history
    mode parsing, mode exit, and completion dismissal
  - thread selector low-risk helpers, including column visibility/order,
    whitespace collapse, truncation, column formatting, column width
    application/cache behavior, thread row compose/selection/click handling,
    delete-confirm actions, title/help/sort labels, current-thread selection,
    search text truncation, fuzzy filtering and sorting, checkpoint-field
    detection and scheduling, thread-list identity matching, filter-control
    cache behavior, action guard rails, page movement, selection, sort
    preference persistence warnings, delete-confirm routing, and empty-list
    exit behavior
  - agent helper logic, including shell allow-list middleware sync/async
    rejection and pass-through, invalid shell allow-list configuration,
    memory-file guard checks for file and shell tools, async-subagent TOML
    loading/validation/read failures, model identity formatting, write/edit
    approval descriptions, web/search/fetch/task/execute approval descriptions
    including Unicode/URL warnings, MCP interrupt registration, and compact
    approval toggling
  - CLI agent assembly, including agent-list JSON/text output, prompt
    sandbox/non-interactive/cwd-fallback branches, default and malformed async
    subagent config handling, shell allow-list middleware fallback behavior,
    runtime subagent injection, memory store path selection, skills source
    precedence including Claude skill roots, local shell/filesystem/sandbox
    backend routing, auto-approve/HITL interrupt selection, scheduler store
    scoping, and custom middleware/subagent plumbing
  - textual adapter helpers, including HITL/ask-user adapter caching, usage
    table output, summarization/internal chunk filtering, tool-id
    normalization, transient stream error detection, message-store tool
    updates, pending-tool error finalization, interrupted AI message
    reconstruction, and mentioned-file embedding/large-file references
  - CLI main entry helpers and dispatch paths, including ripgrep install hints
    across macOS/Linux/Windows/fallback package managers, TUI warning
    formatting, lazy help action dispatch, stdin missing/unreadable/invalid
    text/TTY-restore failure paths, MCP metadata preload context and cleanup
    failures, UTF-8 locale no-op/fallback/warning behavior, fast version
    output, model/profile JSON validation, MCP option conflicts, quiet/no-stream
    usage errors, shell allow-list application, default-model show/save/clear
    commands, agents/threads dispatch, non-interactive run argument plumbing,
    and interactive Textual launch/teardown hints
  - CLI main dispatch edge cases, including MCP trust context/no-server/EOF
    denial paths, non-session stats guard, empty default-model display,
    help/agents/skills/wecombot/threads-help dispatch, non-interactive and
    interactive sandbox dependency failures, and interactive Textual error
    reporting
  - CLI help-screen output, including top-level help, editable-install
    display, agents/skills/update/threads help screens, command-specific
    option sections, shared JSON/help flags, examples, and skill directory
    precedence text
  - sandbox provider lifecycle paths, including LangSmith API-key
    initialization, template creation and error handling, existing/new
    sandbox connection, unsupported-argument/create/delete failures, startup
    timeout cleanup, Daytona env/config startup, existing-ID rejection,
    readiness timeout cleanup, Modal credential success/failure/partial
    fallback, Modal create/reconnect/startup-failure/terminate flows, Runloop
    create/reconnect/not-found/timeout/shutdown paths, and AgentCore credential
    precheck, session start cleanup, stop warning, and reconnect rejection
    behavior
  - Textual app wrapper and local state boundaries, including runtime-module
    delegation for startup, server, update, message flow, approval, ask-user,
    planner, input, shell, slash command, scheduler, schedule manager, WeCom,
    skill, memory/offload, agent, queue, deferred action, model, UI, and thread
    handlers; plus local status/token updates, message-store synchronization,
    scroll re-anchoring, scheduler idle draining, quit-pending timer state,
    approval key routing, approval cleanup/focus restoration, paste/focus/click
    guards, mouse-selection copy delegation, and `run_textual_app` runner
    argument plumbing
  - thread/session persistence helpers, including home-path fallback,
    legacy DB migration success/failure, cache eviction and fresh-copy behavior,
    required-message-count cache gating, checkpoint summary edge cases,
    JsonPlus serializer caching, batched checkpoint summary loading,
    bad/missing checkpoint payload fallback, checkpoint-derived field cache hits,
    checkpoint-derived field defaulting, UUID7 generation, aiosqlite
    compatibility patching, checkpointer construction, prewarm success and error
    tolerance, text/JSON thread-list output, empty-list filtering messages,
    branch-filtered table titles, dry-run/real thread deletion CLI output, and
    message/prompt cache invalidation after deletion
  - configuration helpers, including charset/glyph cache selection, shell
    allow-list parsing and dangerous-pattern rejection, extra skill directory
    parsing, settings path validation and directory creation, session-state
    toggling, stream metadata/git branch handling, LangSmith project/thread URL
    resolution and URL caching, provider detection, default and memory model
    selection, provider kwargs/base URL/API-key merging, OpenRouter attribution
    defaults, model creation/profile override metadata, settings application,
    model capability warnings/errors, runtime settings reload masking and
    preservation behavior, editable-install metadata caching, custom model
    class import/instantiation failures, LangChain model-init error wrapping,
    DeepSeek parameter normalization, and lazy singleton cache behavior
  - configuration bootstrap helpers, including nearest-project dotenv
    discovery, project/global dotenv precedence without shell override,
    dotenv read-error tolerance, skills `config.toml` extra-dir parsing and
    malformed TOML handling, bootstrap LangSmith project preservation, and
    prefixed LangSmith SDK env-var propagation
  - fixed configuration regressions where LangSmith project resolution read an
    obsolete `invincat_langchain_project` field and OpenRouter setup depended on
    an optional private `deepagents._models.check_openrouter_version` helper
    that is not present in the currently installed SDK
  - update-check lifecycle helpers, including malformed cache tolerance, PyPI
    fetch and write failures, empty release maps, invalid installed/latest
    version comparisons, uv/brew/pip/editable install detection, auto-upgrade
    subprocess success/failure/timeout/OSError paths, config missing/bad/env
    override behavior, missing `requests` fallback, unknown installer handling,
    persisted auto-update preferences, missing-config creation, atomic-write
    temp-file cleanup on replace failure, seen-version read and write failures,
    and invalid what's-new version comparisons
  - Textual adapter execution-boundary helpers, including approve-plan adapter
    caching, assistant-text flush creation/finalization/store sync, context-token
    persistence retries, thread ensuring, connectivity-error backoff, token
    display show/update behavior, interrupt cleanup state capture, in-flight
    tool rejection, spinner/message cleanup, transient pre-stream retry, main
    assistant text streaming, external text-delta callbacks, active-message
    clearing, hook dispatch, unexpected stream-error cleanup, cancellation
    cleanup routing, invalid HITL interrupt aborts, ask-user no-UI error
    resumes, ask-user answered tool success, approve-plan approved tool
    success, ask-user invalid/cancelled/non-dict widget results, approve-plan
    rejected resumes, HITL approve/reject routing, session auto-approve,
    auto-approve-all state sync, HITL file-operation approval tagging,
    permission-request hook dispatch, shielded token-persist suppression, and
    stats wall-time recording
  - Textual adapter tool-result boundaries, including direct tool-call-id
    matching, widget-attribute fallback matching, message-store tool-id/status
    sync, success/error widget completion, unmatched ToolMessage fallback
    mounting, WeCom file-request payload de-duplication and callback failure
    tolerance, schedule tool payload callback success/failure paths,
    summarization and internal-memory stream status transitions, summary notice
    mounting when streams resume or end mid-summary, file-operation missing
    tracking annotations, file-operation readback error annotations, diff
    message mounting, stream token-usage accumulation, immediate token display
    updates, context-token persistence, no-content-block skip handling,
    tool-error hook dispatch, and current-tool cleanup
  - Textual adapter low-risk setup/filtering paths, including single-model and
    wall-time-only usage output, internal chunk `None` handling, missing
    message-store tool lookups, interrupted tool-only AI reconstruction,
    mentioned-file read failures, referenced-file injection errors, multimodal
    content preparation, partial token-callback warning paths, memory-update
    custom events, and noisy stream-chunk skips
  - message widget behavior, including tool-call state transitions for
    generating/running/success/error/rejected/skipped states, shell error
    command prefixing, redundant success-trailer stripping, output preview and
    full expansion rendering, truncation/collapse hints, click toggling,
    write/edit argument filtering, deferred argument finalization before mount,
    and custom tool output prefixing
  - message widget helper behavior, including skill compose optional sections,
    assistant markdown streaming/reasoning/set-content helpers, diff compose
    hints and click/key routing, tool compose variants, no-output timestamp
    click fallback, deferred tool-state restoration, animation state text, and
    short/blank/truncated output display paths
  - chat-input widget behavior, including completion popup deferred rebuilds,
    stale-generation cancellation and mount-failure recovery, placeholder-span
    deletion helpers, paste-burst buffering and flushing, paste flush exception
    fallback, focus/timer cleanup, VSCode shift-enter backslash compatibility,
    completion-active navigation guards, paste event path parsing, dropped-path
    command-mode recovery, external paste routing, media placeholder insertion
    for submitted and inline paths, mode-prefix stripping, history navigation,
    completion click handling, default history path selection, mount-time
    controller wiring, slash-command update warnings, invalid media attachment
    notifications, text-change guard branches, and key handling for
    submissions and mode exits
  - thread-selector widget behavior, including cached cell rendering, no-match
    row selection updates, input filtering and submitted-filter selection,
    alphabetic key routing back to search, search-selection collapse,
    filter-control focus cycling, sort/relative-time/column checkbox routing,
    filter-and-rebuild worker behavior, checkpoint-detail enrichment, LangSmith
    title URL updates, style-link click routing, thread deletion success and
    failure handling, option-click selection, and cancel behavior
  - theme registry and color-resolution behavior, including `ThemeColors`
    validation and merge semantics, `ThemeEntry` label validation, user theme
    config parsing for built-in overrides and custom themes, invalid TOML and
    home-directory failure tolerance, registry freezing/reload, CSS variable
    defaults, missing built-in override tolerance, empty/invalid built-in
    override handling, widget-to-app resolution, Textual theme hex fallback
    behavior, and `get_theme_colors()` custom/built-in/fallback paths
  - status-bar widget behavior, including model-label empty/full/truncated
    rendering, wide-character right-truncation, mode/plan/auto-approve/message
    watcher updates and missing-widget tolerance, cwd home-path formatting and
    home lookup failure fallback, token formatting across plain/K/M values and
    no-limit/warn/danger states, token hide/forced-refresh paths, mount-time
    model initialization, model/memory-model sync behavior, resize visibility,
    and state setter wrappers
  - memory-viewer widget behavior, including missing/unreadable/invalid memory
    store snapshots, invalid item filtering, legacy timestamp normalization,
    atomic delete schema validation, mount/unmount refresh handling, navigation
    and scope/sort toggles, delete confirmation success/error paths, and
    snapshot rendering for no-scope, missing, invalid, empty-valid, valid-item,
    status-message, and pending-delete help states
  - autocomplete widget behavior, including invalid-cursor reset guards,
    slash-command navigation edge keys, fuzzy file dotfile/depth scoring,
    project-file fallback error tolerance, cache refresh and warm-cache failure
    handling, shell path escaping/token parsing, home-directory completion for
    `~/...`, path suggestion error handling, single-suggestion tab application,
    and shell completion edge-key routing
  - message widget behavior, including timestamp click guards, mode-color
    fallbacks, ASCII mount styling, skill-body mount/deferred expansion,
    assistant markdown stream lazy initialization, tool-call mount branches,
    deferred status restore, output truncation hints, argument update paths,
    non-shell error output, and app-message link/timestamp click handling
  - thread-selector widget behavior, including fuzzy matcher failure
    fallbacks, thread loading success/error paths, cached checkpoint
    enrichment failures, mount-time worker routing, missing-DOM early returns,
    LangSmith URL failures, cell-label refresh, help/header rebuilds, list
    rebuild empty/non-empty states, selected-row scrolling, and mount-error
    rendering fallbacks
  - Textual adapter streaming behavior, including invalid ask-user and
    approve-plan interrupt recovery, ToolMessage fallback matching by tool
    name, index-to-ID tool-call re-keying, file-op tracker re-keying,
    text-delta callback failures, HumanMessage-triggered pending-text flush,
    unknown tool-call IDs, scalar tool args, plan-mode blocked tools, and
    summarization/memory middleware cleanup fallbacks
  - memory-agent middleware and store behavior, including validation reject
    branches, rescore/retier/archive normalization, missing user-store
    creation, malformed delete stores, blank update no-ops, archived memory
    reactivation, cursor reset/advance behavior, file-update and wall-clock
    throttling, config lookup fallback, cleanup-only after-agent returns, and
    context-window preservation of the latest human request
  - approval, output-formatting, media, message widget, and message-store
    edge coverage, including expandable shell-command help text, non-minimal
    approval mount refresh, outside-CWD search path filename fallback, JPG
    image-format normalization, slash-command styling without a mode prefix,
    markdown-render fallback, assistant lazy markdown/reasoning queries,
    tool-argument update error logging, unknown message-type serialization
    fallback, small bulk-load behavior, `None` tool-call IDs, and archived
    hydration guards
  - memory-viewer final branch coverage, including direct compose traversal
    for title/list/help widgets and fallback to the first configured scope
    when the current scope is unavailable
  - welcome-banner final branch coverage, including mount-time theme watching
    and LangSmith worker scheduling, click link forwarding, editable-install
    banner fallback when the version tag is absent, and ANSI-themed linked
    project rendering
  - agent helper final branch coverage, including async shell allow-list and
    memory-file guard rejection early returns, explicit working-directory
    prompt rendering, unreplaced system-prompt placeholder warnings, and long
    hidden-Unicode execute-command raw preview truncation
  - input, autocomplete, configuration, and model-config final branch coverage,
    including paste parsing edge cases, unreachable defensive guards marked out
    of coverage, shell/file autocomplete boundaries, singleton cache
    re-checks, dotenv and LangSmith timeout/error paths, model config read/save
    fallbacks, target-specific model-param wrappers, provider registration for
    new files, and class-path provider profile import failures
  - sandbox factory, CLI main, Textual app wrapper, chat input, and thread
    selector final branch coverage, including setup-script execution,
    provider dependency/API-key and readiness failures, CLI version lookup and
    optional rich import fallbacks, Textual app decoder/message/remote-agent
    wrappers, app exit/paste/focus guards, chat compose/key/path/media guard
    paths, and thread-selector compose/table-pane/mount-error branches
  - memory-agent score/tier update application, ensuring explicit tiers remain
    authoritative while scores are clamped into the requested tier band

## Verification Evidence

- `python -m ruff check .`: passing
- `python -m ruff format --check .`: passing
- `python -m mypy`: passing
- `python -m pytest -q`: passing with 2190 tests
- `python -m coverage run -m pytest -q`: passing with 2190 tests
- `python -m coverage report --include='invincat_cli/*' --skip-empty`:
  99% total application-code coverage, with
  `invincat_cli/agent.py` at 100%,
  `invincat_cli/app.py` at 100%,
  `invincat_cli/io/file_ops.py` at 100%,
  `invincat_cli/io/media_utils.py` at 100%,
  `invincat_cli/io/input.py` at 100%,
  `invincat_cli/auto_memory.py` at 100%,
  `invincat_cli/model_config.py` at 100%,
  `invincat_cli/config.py` at 100%,
  `invincat_cli/integrations/sandbox_factory.py` at 100%,
  `invincat_cli/main.py` at 99%,
  `invincat_cli/sessions.py` at 100%,
  `invincat_cli/widgets/messages.py` at 100%,
  `invincat_cli/widgets/message_store.py` at 100%,
  `invincat_cli/widgets/thread_selector.py` at 100%,
  `invincat_cli/textual_adapter.py` at 92%,
  `invincat_cli/memory_agent.py` at 92%,
  `invincat_cli/models/testing.py` at 100%,
  `invincat_cli/skills/commands.py` at 100%,
  `invincat_cli/theme.py` at 100%,
  `invincat_cli/update_check.py` at 100%,
  `invincat_cli/widgets/approval.py` at 100%,
  `invincat_cli/widgets/autocomplete.py` at 100%,
  `invincat_cli/widgets/chat_input.py` at 100%,
  `invincat_cli/widgets/memory_viewer.py` at 100%,
  `invincat_cli/widgets/output_formatters.py` at 100%,
  `invincat_cli/widgets/status.py` at 100%,
  `invincat_cli/widgets/welcome.py` at 100%,
  `invincat_cli/wecom/daemon.py` at 90%
- `git diff --check`: passing

## Remaining Risks

The project still has some meaningful coverage gaps. These are not all good
candidates for narrow unit tests because they contain UI orchestration,
process/server startup, remote/network integration, or large handler flows.

Highest remaining gaps:

- Remaining `invincat_cli/textual_adapter.py` interrupt-cancellation and
  connectivity retry edge cases still need higher-level integration coverage.
- Remaining `invincat_cli/memory_agent.py` middleware lifecycle branches,
  especially async model invocation, recovery, throttling, and cleanup paths,
  need broader integration-style tests rather than more narrow pure-helper
  assertions.
- Remaining clipboard, editor, and platform-specific edge cases need
  filesystem/OS-level tests.
- Remaining model-provider integration paths still need stronger branch and
  failure-mode coverage with real provider packages and network-independent
  contract tests.
- `remote/server` integration paths and the remaining WeCom daemon/headless
  socket, signal, and external-service flows still rely on indirect coverage.
- `invincat_cli/main.py` has only its script-entry guard uncovered by line
  coverage; the CLI behavior behind that entrypoint is otherwise covered
  through direct entrypoint tests.

## Completion Status

The codebase is currently lint-clean, format-clean, type-check clean, and the
full unit test suite is green. The code-level review and optimization pass is
complete against the requested modularity, maintainability, extensibility, style,
architecture, and engineering-practice criteria.

The remaining items above are residual integration risks, not known code-quality
defects found in this pass. They should be tracked as follow-up acceptance tests
that exercise real terminals, subprocesses, sockets, model providers, clipboard
tools, and WeCom bridge behavior in environments that unit tests intentionally
mock or isolate.

## Completion Audit

- Objective: review the current project comprehensively, optimize code directly,
  and explain key improvements.
- Modularity and architecture evidence: runtime/UI responsibilities have been
  split into focused `invincat_cli/app_runtime/` modules, scheduler/server/WeCom
  helpers are isolated behind explicit modules, and `DeepAgentsApp` now exposes
  its cross-module runtime contract through typed attributes.
- Maintainability and extensibility evidence: broad behavioral tests now cover
  app handlers, scheduling, model configuration, memory/offload, tool display,
  file operations, WeCom flows, widgets, CLI entrypoints, and server helpers.
- Code-standard evidence: `python -m ruff check .`,
  `python -m ruff format --check .`, `python -m mypy`, `python -m compileall -q
  invincat_cli tests`, and `git diff --check` all pass.
- Behavioral evidence: `PYTHONPATH=. pytest -q` passes with 2190 tests.
- Coverage evidence: `python -m coverage report --include='invincat_cli/*'
  --skip-empty` reports 99% total application-code coverage.
- Remaining uncovered requirements: no explicit user requirement remains
  unaddressed. The documented residual risks require external integration
  environments and are follow-up hardening work rather than blockers for this
  code-quality pass.
