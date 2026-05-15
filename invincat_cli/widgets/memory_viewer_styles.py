"""CSS for the memory viewer modal."""

MEMORY_VIEWER_CSS = """
MemoryViewerScreen {
    align: left top;
}

MemoryViewerScreen > Vertical {
    width: 100%;
    height: 100%;
    background: $surface;
    border: none;
    padding: 1 2;
}

MemoryViewerScreen .memory-title {
    text-style: bold;
    color: $primary;
    text-align: center;
    margin-bottom: 1;
}

MemoryViewerScreen .memory-summary {
    color: $text-muted;
    margin-bottom: 1;
}

MemoryViewerScreen .memory-list {
    height: 1fr;
    min-height: 8;
    background: $background;
    scrollbar-gutter: stable;
    padding: 0 1;
}

MemoryViewerScreen .memory-help {
    height: 1;
    color: $text-muted;
    text-style: italic;
    margin-top: 1;
    text-align: center;
}
"""
