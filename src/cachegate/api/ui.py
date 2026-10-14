"""Interactive Web Playground UI for CacheGate.

Provides a developer playground served at `/` and `/ui` to test chat completions,
inspect cache hits/misses, observe latency reductions, and generate API keys.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])

PLAYGROUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CacheGate &bull; Semantic LLM Gateway Playground</title>
  <meta name="description" content="Interactive developer playground for CacheGate LLM Gateway with exact and semantic caching, multi-provider routing, and live metrics.">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-base: #07090e;
      --bg-surface: #0e131f;
      --bg-surface-elevated: #161d2f;
      --bg-glass: rgba(18, 24, 38, 0.75);
      --border-subtle: rgba(255, 255, 255, 0.08);
      --border-focus: rgba(99, 102, 241, 0.5);
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --primary: #6366f1;
      --primary-hover: #4f46e5;
      --primary-glow: rgba(99, 102, 241, 0.25);
      --accent-cyan: #06b6d4;
      --accent-emerald: #10b981;
      --accent-amber: #f59e0b;
      --accent-rose: #f43f5e;
      --radius-sm: 8px;
      --radius-md: 12px;
      --radius-lg: 16px;
      --shadow-lg: 0 20px 40px -15px rgba(0, 0, 0, 0.5);
      --font-sans: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background: radial-gradient(circle at 15% 15%, rgba(99, 102, 241, 0.12) 0%, transparent 40%),
                  radial-gradient(circle at 85% 85%, rgba(6, 182, 212, 0.08) 0%, transparent 40%),
                  var(--bg-base);
      color: var(--text-main);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }

    /* Header */
    header {
      background: var(--bg-glass);
      backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--border-subtle);
      position: sticky;
      top: 0;
      z-index: 50;
      padding: 0.85rem 1.75rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      text-decoration: none;
      color: var(--text-main);
    }

    .brand-icon {
      width: 36px;
      height: 36px;
      border-radius: var(--radius-sm);
      background: linear-gradient(135deg, var(--primary), var(--accent-cyan));
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 1.1rem;
      color: #fff;
      box-shadow: 0 0 20px var(--primary-glow);
    }

    .brand-title {
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .brand-badge {
      font-size: 0.68rem;
      padding: 0.15rem 0.5rem;
      border-radius: 9999px;
      background: rgba(99, 102, 241, 0.18);
      color: #a5b4fc;
      border: 1px solid rgba(99, 102, 241, 0.35);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    .status-pill {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      font-size: 0.8rem;
      padding: 0.35rem 0.75rem;
      border-radius: 9999px;
      background: rgba(16, 185, 129, 0.1);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: var(--accent-emerald);
      font-weight: 500;
    }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent-emerald);
      box-shadow: 0 0 10px var(--accent-emerald);
      animation: pulse 2s infinite ease-in-out;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
    }

    .btn-ghost {
      background: transparent;
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      padding: 0.4rem 0.85rem;
      border-radius: var(--radius-sm);
      font-size: 0.82rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s ease;
    }

    .btn-ghost:hover {
      background: rgba(255, 255, 255, 0.05);
      color: var(--text-main);
      border-color: rgba(255, 255, 255, 0.2);
    }

    /* Main Grid Layout */
    .app-container {
      flex: 1;
      display: grid;
      grid-template-columns: 320px 1fr 340px;
      height: calc(100vh - 65px);
      overflow: hidden;
    }

    @media (max-width: 1200px) {
      .app-container {
        grid-template-columns: 280px 1fr;
      }
      .inspector-panel {
        display: none !important;
      }
    }

    @media (max-width: 860px) {
      .app-container {
        grid-template-columns: 1fr;
        height: auto;
      }
      .config-sidebar {
        border-right: none !important;
        border-bottom: 1px solid var(--border-subtle);
      }
    }

    /* Left Sidebar: Controls */
    .config-sidebar {
      background: var(--bg-surface);
      border-right: 1px solid var(--border-subtle);
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
      overflow-y: auto;
    }

    .panel-heading {
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 0.6rem;
    }

    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
    }

    .form-label {
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .form-input, .form-select {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 0.55rem 0.75rem;
      color: var(--text-main);
      font-size: 0.88rem;
      outline: none;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
      font-family: inherit;
    }

    .form-input:focus, .form-select:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 2px var(--primary-glow);
    }

    .slider-wrap {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    input[type="range"] {
      flex: 1;
      accent-color: var(--primary);
      cursor: pointer;
    }

    .range-val {
      font-family: var(--font-mono);
      font-size: 0.82rem;
      color: var(--accent-cyan);
      min-width: 30px;
      text-align: right;
    }

    .toggle-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: var(--bg-surface-elevated);
      padding: 0.65rem 0.85rem;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-subtle);
    }

    .toggle-switch {
      position: relative;
      display: inline-block;
      width: 40px;
      height: 22px;
    }

    .toggle-switch input {
      opacity: 0;
      width: 0;
      height: 0;
    }

    .toggle-slider {
      position: absolute;
      cursor: pointer;
      top: 0; left: 0; right: 0; bottom: 0;
      background-color: #273147;
      transition: .2s;
      border-radius: 22px;
    }

    .toggle-slider:before {
      position: absolute;
      content: "";
      height: 16px;
      width: 16px;
      left: 3px;
      bottom: 3px;
      background-color: white;
      transition: .2s;
      border-radius: 50%;
    }

    input:checked + .toggle-slider {
      background-color: var(--primary);
    }

    input:checked + .toggle-slider:before {
      transform: translateX(18px);
    }

    .info-card {
      background: rgba(99, 102, 241, 0.08);
      border: 1px solid rgba(99, 102, 241, 0.2);
      border-radius: var(--radius-sm);
      padding: 0.75rem;
      font-size: 0.78rem;
      line-height: 1.45;
      color: #c7d2fe;
    }

    .key-gen-box {
      display: flex;
      gap: 0.5rem;
    }

    .btn-primary {
      background: linear-gradient(135deg, var(--primary), var(--primary-hover));
      color: #fff;
      border: none;
      border-radius: var(--radius-sm);
      padding: 0.55rem 0.85rem;
      font-weight: 600;
      font-size: 0.82rem;
      cursor: pointer;
      transition: filter 0.15s ease;
      white-space: nowrap;
    }

    .btn-primary:hover {
      filter: brightness(1.1);
    }

    /* Center: Chat Playground */
    .chat-center {
      display: flex;
      flex-direction: column;
      height: 100%;
      background: var(--bg-base);
      position: relative;
    }

    .chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: 1.5rem 2rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }

    .welcome-hero {
      margin: auto 0;
      text-align: center;
      max-width: 600px;
      align-self: center;
      padding: 2rem 1rem;
    }

    .hero-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      background: rgba(99, 102, 241, 0.15);
      border: 1px solid rgba(99, 102, 241, 0.3);
      padding: 0.35rem 0.85rem;
      border-radius: 9999px;
      font-size: 0.78rem;
      color: #c7d2fe;
      font-weight: 600;
      margin-bottom: 1rem;
    }

    .hero-title {
      font-size: 1.9rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      margin-bottom: 0.6rem;
      background: linear-gradient(135deg, #fff 30%, #94a3b8);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .hero-sub {
      color: var(--text-muted);
      font-size: 0.95rem;
      line-height: 1.5;
      margin-bottom: 1.75rem;
    }

    .quick-prompts {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 0.75rem;
      text-align: left;
    }

    .quick-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 0.85rem 1rem;
      cursor: pointer;
      transition: all 0.15s ease;
    }

    .quick-card:hover {
      border-color: var(--border-focus);
      background: var(--bg-surface-elevated);
      transform: translateY(-2px);
    }

    .quick-card-title {
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text-main);
      margin-bottom: 0.25rem;
    }

    .quick-card-desc {
      font-size: 0.75rem;
      color: var(--text-dim);
    }

    /* Message Bubbles */
    .message-row {
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
      animation: fadeIn 0.2s ease-out;
    }

    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .message-row.user {
      align-items: flex-end;
    }

    .message-row.assistant {
      align-items: flex-start;
    }

    .message-bubble {
      max-width: 82%;
      padding: 0.9rem 1.15rem;
      border-radius: var(--radius-md);
      font-size: 0.92rem;
      line-height: 1.55;
      word-break: break-word;
      white-space: pre-wrap;
    }

    .message-row.user .message-bubble {
      background: linear-gradient(135deg, var(--primary), #4338ca);
      color: #fff;
      border-bottom-right-radius: 4px;
      box-shadow: 0 4px 15px rgba(99, 102, 241, 0.2);
    }

    .message-row.assistant .message-bubble {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      color: var(--text-main);
      border-bottom-left-radius: 4px;
    }

    /* Telemetry Pill Bar */
    .telemetry-bar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
      font-size: 0.75rem;
      font-family: var(--font-mono);
      margin-top: 0.25rem;
    }

    .badge-cache-exact {
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.4);
      color: var(--accent-emerald);
      padding: 0.18rem 0.5rem;
      border-radius: 9999px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      box-shadow: 0 0 10px rgba(16, 185, 129, 0.2);
    }

    .badge-cache-semantic {
      background: rgba(6, 182, 212, 0.15);
      border: 1px solid rgba(6, 182, 212, 0.4);
      color: var(--accent-cyan);
      padding: 0.18rem 0.5rem;
      border-radius: 9999px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      box-shadow: 0 0 10px rgba(6, 182, 212, 0.2);
    }

    .badge-cache-miss {
      background: rgba(245, 158, 11, 0.15);
      border: 1px solid rgba(245, 158, 11, 0.4);
      color: var(--accent-amber);
      padding: 0.18rem 0.5rem;
      border-radius: 9999px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
    }

    .badge-pill {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      padding: 0.18rem 0.5rem;
      border-radius: 9999px;
      font-size: 0.72rem;
    }

    .badge-speedup {
      background: rgba(16, 185, 129, 0.1);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
      padding: 0.18rem 0.5rem;
      border-radius: 9999px;
      font-weight: 600;
    }

    .btn-inspect {
      background: transparent;
      border: 1px solid var(--border-subtle);
      color: var(--text-dim);
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      cursor: pointer;
      font-size: 0.7rem;
      transition: all 0.15s ease;
    }

    .btn-inspect:hover {
      color: var(--text-main);
      border-color: var(--border-focus);
    }

    /* Chat Input Area */
    .chat-input-deck {
      border-top: 1px solid var(--border-subtle);
      background: var(--bg-surface);
      padding: 1rem 1.75rem 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
    }

    .input-row {
      display: flex;
      gap: 0.75rem;
      align-items: flex-end;
    }

    .chat-textarea {
      flex: 1;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 0.75rem 1rem;
      color: var(--text-main);
      font-size: 0.92rem;
      outline: none;
      resize: none;
      font-family: inherit;
      max-height: 180px;
      min-height: 52px;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }

    .chat-textarea:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 2px var(--primary-glow);
    }

    .btn-send {
      background: linear-gradient(135deg, var(--primary), var(--accent-cyan));
      border: none;
      border-radius: var(--radius-md);
      width: 52px;
      height: 52px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      cursor: pointer;
      transition: transform 0.15s ease, filter 0.15s ease;
      box-shadow: 0 4px 15px var(--primary-glow);
    }

    .btn-send:hover {
      filter: brightness(1.1);
      transform: scale(1.02);
    }

    .btn-send:disabled {
      opacity: 0.4;
      cursor: not-allowed;
      transform: none;
    }

    .input-hint-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.74rem;
      color: var(--text-dim);
    }

    /* Right Sidebar: Inspector */
    .inspector-panel {
      background: var(--bg-surface);
      border-left: 1px solid var(--border-subtle);
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
      overflow-y: auto;
    }

    .metric-card {
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }

    .metric-title {
      font-size: 0.72rem;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 600;
    }

    .metric-val {
      font-size: 1.4rem;
      font-weight: 700;
      color: var(--text-main);
      font-family: var(--font-mono);
    }

    .metric-sub {
      font-size: 0.72rem;
      color: var(--accent-emerald);
    }

    pre.json-view {
      background: #05070a;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 0.75rem;
      font-family: var(--font-mono);
      font-size: 0.74rem;
      color: #93c5fd;
      overflow-x: auto;
      max-height: 380px;
      line-height: 1.45;
    }

    /* Toast Notification */
    .toast {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: var(--bg-surface-elevated);
      border: 1px solid var(--border-focus);
      color: #fff;
      padding: 0.75rem 1.25rem;
      border-radius: var(--radius-sm);
      box-shadow: var(--shadow-lg);
      font-size: 0.85rem;
      display: none;
      z-index: 100;
      animation: fadeIn 0.2s ease;
    }
  </style>
</head>
<body>

  <!-- Header -->
  <header>
    <a href="/" class="brand">
      <div class="brand-icon">&lambda;</div>
      <div class="brand-title">
        SemanticGate
        <span class="brand-badge">CacheGate v0.1.0</span>
      </div>
    </a>
    <div class="header-actions">
      <div class="status-pill">
        <div class="status-dot"></div>
        <span id="gateway-status">Gateway Online</span>
      </div>
      <a href="/docs" target="_blank" class="btn-ghost" title="OpenAPI Swagger UI">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
        Swagger Docs
      </a>
      <a href="/metrics" target="_blank" class="btn-ghost" title="Prometheus Metrics">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke_width="2"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
        Metrics
      </a>
    </div>
  </header>

  <!-- App Body Layout -->
  <div class="app-container">

    <!-- Left Controls Panel -->
    <aside class="config-sidebar">
      <div>
        <div class="panel-heading">Model &amp; Routing</div>
        <div class="form-group">
          <label class="form-label" for="model-select">Target Model Alias</label>
          <select id="model-select" class="form-select">
            <option value="mock-fast" selected>mock-fast (Mock echo provider)</option>
            <option value="gpt-4o-mini">gpt-4o-mini (OpenAI / mock)</option>
            <option value="claude-haiku">claude-haiku (Anthropic / mock)</option>
          </select>
        </div>
      </div>

      <div>
        <div class="panel-heading">Generation Parameters</div>
        <div class="form-group">
          <div class="form-label">
            <span>Temperature</span>
            <span id="temp-val" class="range-val">0.0</span>
          </div>
          <div class="slider-wrap">
            <input type="range" id="temp-slider" min="0.0" max="1.0" step="0.05" value="0.0">
          </div>
          <div id="temp-note" style="font-size:0.7rem; color:var(--accent-emerald);">
            &check; &le; 0.3: Eligible for caching
          </div>
        </div>

        <div class="form-group" style="margin-top: 0.75rem;">
          <label class="form-label" for="max-tokens-input">Max Tokens</label>
          <input type="number" id="max-tokens-input" class="form-input" value="150" min="1" max="4096">
        </div>

        <div class="form-group" style="margin-top: 0.75rem;">
          <div class="toggle-row">
            <span style="font-size:0.82rem; font-weight:600; color:var(--text-muted);">Stream (SSE)</span>
            <label class="toggle-switch">
              <input type="checkbox" id="stream-toggle">
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>
      </div>

      <div>
        <div class="panel-heading">Authentication</div>
        <div class="form-group">
          <label class="form-label" for="api-key-input">Client API Key</label>
          <input type="text" id="api-key-input" class="form-input" value="change-me" placeholder="cg-... or change-me">
        </div>
        <div style="margin-top: 0.5rem;" class="key-gen-box">
          <button id="btn-gen-key" class="btn-primary" style="flex:1;">+ Generate Key</button>
        </div>
      </div>

      <div class="info-card">
        <strong>Cache Acceleration:</strong> Exact cache matches are resolved directly via Redis in single-digit milliseconds. When enabled, semantic caching performs vector similarity lookups to match related queries.
      </div>
    </aside>

    <!-- Center Chat Area -->
    <main class="chat-center">
      <div id="chat-messages" class="chat-messages">
        <!-- Hero Welcome -->
        <div id="welcome-hero" class="welcome-hero">
          <div class="hero-badge">&bull; Real-time Cache Optimization &bull;</div>
          <h1 class="hero-title">High-Velocity LLM Gateway</h1>
          <p class="hero-sub">Test chat completions through CacheGate with automatic exact deduplication, semantic vector matching, and sub-5ms cached latency.</p>
          
          <div class="quick-prompts">
            <div class="quick-card" onclick="sendQuickPrompt('Test Cache Acceleration: What is CacheGate?')">
              <div class="quick-card-title">&rarr; Test Cache Acceleration</div>
              <div class="quick-card-desc">Send once for cold call, send again for 100x cache hit speedup</div>
            </div>
            <div class="quick-card" onclick="sendQuickPrompt('Explain how semantic caching works in LLM gateways.')">
              <div class="quick-card-title">&rarr; Semantic Vector Caching</div>
              <div class="quick-card-desc">Query similar concepts to observe cosine similarity indexing</div>
            </div>
            <div class="quick-card" onclick="sendQuickPrompt('Write a Python function to compute Fibonacci numbers.')">
              <div class="quick-card-title">&rarr; Code Generation</div>
              <div class="quick-card-desc">Test deterministic mock &amp; streaming token output</div>
            </div>
            <div class="quick-card" onclick="sendQuickPrompt('What are token bucket rate limiters?')">
              <div class="quick-card-title">&rarr; Gateway Architecture</div>
              <div class="quick-card-desc">Check rate limiting and tenant isolation headers</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Chat Input Deck -->
      <div class="chat-input-deck">
        <div class="input-row">
          <textarea id="chat-input" class="chat-textarea" placeholder="Type your prompt here... (Press Enter to send, Shift+Enter for newline)"></textarea>
          <button id="btn-send" class="btn-send" title="Send request">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
          </button>
        </div>
        <div class="input-hint-row">
          <span>Routing via: <strong id="hint-provider" style="color:var(--accent-cyan);">mock-fast</strong></span>
          <button id="btn-clear-chat" class="btn-ghost" style="padding:0.2rem 0.5rem; font-size:0.72rem;">Clear Chat</button>
        </div>
      </div>
    </main>

    <!-- Right Inspector Panel -->
    <aside class="inspector-panel">
      <div class="panel-heading">Live Telemetry</div>
      
      <div class="metric-card">
        <div class="metric-title">Last Latency</div>
        <div id="metric-latency" class="metric-val">-- ms</div>
        <div id="metric-cache-badge" class="metric-sub">Waiting for request...</div>
      </div>

      <div class="metric-card">
        <div class="metric-title">Speedup Ratio</div>
        <div id="metric-speedup" class="metric-val" style="color:var(--accent-emerald);">--</div>
        <div id="metric-provider" class="metric-sub" style="color:var(--text-dim);">Upstream: --</div>
      </div>

      <div class="panel-heading" style="margin-top:0.5rem;">Raw Response Headers</div>
      <pre id="headers-view" class="json-view" style="max-height:160px;">// Response headers will appear here...</pre>

      <div class="panel-heading" style="margin-top:0.5rem;">Response Payload</div>
      <pre id="payload-view" class="json-view">// Response JSON will appear here...</pre>
    </aside>

  </div>

  <div id="toast" class="toast"></div>

  <script>
    const chatMessages = document.getElementById('chat-messages');
    const chatInput = document.getElementById('chat-input');
    const btnSend = document.getElementById('btn-send');
    const btnClear = document.getElementById('btn-clear-chat');
    const modelSelect = document.getElementById('model-select');
    const tempSlider = document.getElementById('temp-slider');
    const tempVal = document.getElementById('temp-val');
    const tempNote = document.getElementById('temp-note');
    const maxTokensInput = document.getElementById('max-tokens-input');
    const streamToggle = document.getElementById('stream-toggle');
    const apiKeyInput = document.getElementById('api-key-input');
    const btnGenKey = document.getElementById('btn-gen-key');
    const welcomeHero = document.getElementById('welcome-hero');
    const metricLatency = document.getElementById('metric-latency');
    const metricCacheBadge = document.getElementById('metric-cache-badge');
    const metricSpeedup = document.getElementById('metric-speedup');
    const metricProvider = document.getElementById('metric-provider');
    const headersView = document.getElementById('headers-view');
    const payloadView = document.getElementById('payload-view');
    const hintProvider = document.getElementById('hint-provider');
    const toast = document.getElementById('toast');

    let coldCallLatencies = {};

    function showToast(msg) {
      toast.innerText = msg;
      toast.style.display = 'block';
      setTimeout(() => { toast.style.display = 'none'; }, 3200);
    }

    // Temperature changes
    tempSlider.addEventListener('input', (e) => {
      const val = parseFloat(e.target.value);
      tempVal.innerText = val.toFixed(2);
      if (val > 0.3) {
        tempNote.innerHTML = '<span style="color:var(--accent-amber);">&#9888; &gt; 0.3: Cache skipped per policy</span>';
      } else {
        tempNote.innerHTML = '<span style="color:var(--accent-emerald);">&check; &le; 0.3: Eligible for caching</span>';
      }
    });

    modelSelect.addEventListener('change', () => {
      hintProvider.innerText = modelSelect.value;
    });

    // Auto-expand textarea
    chatInput.addEventListener('input', () => {
      chatInput.style.height = 'auto';
      chatInput.style.height = Math.min(chatInput.scrollHeight, 180) + 'px';
    });

    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    btnSend.addEventListener('click', sendMessage);

    btnClear.addEventListener('click', () => {
      chatMessages.innerHTML = '';
      if (welcomeHero) chatMessages.appendChild(welcomeHero);
    });

    // Key generator
    btnGenKey.addEventListener('click', async () => {
      try {
        btnGenKey.disabled = true;
        btnGenKey.innerText = 'Creating...';
        const resp = await fetch('/admin/keys', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Admin-Key': 'change-me'
          },
          body: JSON.stringify({ name: 'playground-key-' + Date.now().toString().slice(-4), tenant_id: 'playground' })
        });
        const data = await resp.json();
        if (data.key) {
          apiKeyInput.value = data.key;
          showToast('New API Key generated & activated!');
        } else {
          showToast('Error generating key: ' + JSON.stringify(data));
        }
      } catch (err) {
        showToast('Request failed: ' + err.message);
      } finally {
        btnGenKey.disabled = false;
        btnGenKey.innerText = '+ Generate Key';
      }
    });

    function sendQuickPrompt(promptText) {
      chatInput.value = promptText;
      sendMessage();
    }

    async function sendMessage() {
      const text = chatInput.value.trim();
      if (!text) return;

      if (welcomeHero && welcomeHero.parentNode) {
        welcomeHero.parentNode.removeChild(welcomeHero);
      }

      // Append User message
      appendMessage('user', text);
      chatInput.value = '';
      chatInput.style.height = '52px';
      btnSend.disabled = true;

      const model = modelSelect.value;
      const temperature = parseFloat(tempSlider.value);
      const max_tokens = parseInt(maxTokensInput.value, 10) || 150;
      const isStream = streamToggle.checked;
      const apiKey = apiKeyInput.value.trim();

      const headers = {
        'Content-Type': 'application/json'
      };
      if (apiKey) {
        headers['Authorization'] = 'Bearer ' + apiKey;
      }

      const requestBody = {
        model: model,
        messages: [{ role: 'user', content: text }],
        temperature: temperature,
        max_tokens: max_tokens,
        stream: isStream
      };

      const startTime = performance.now();

      try {
        const resp = await fetch('/v1/chat/completions', {
          method: 'POST',
          headers: headers,
          body: JSON.stringify(requestBody)
        });

        const clientDuration = (performance.now() - startTime).toFixed(1);
        const cacheStatus = resp.headers.get('x-cache') || 'MISS';
        const provider = resp.headers.get('x-provider') || 'unknown';
        const gatewayLatency = resp.headers.get('x-gateway-latency-ms') || clientDuration;
        const requestId = resp.headers.get('x-request-id') || 'n/a';
        const rateLimitRemaining = resp.headers.get('x-ratelimit-remaining') || 'n/a';

        // Collect headers for inspector
        const headerObj = {};
        for (const [k, v] of resp.headers.entries()) {
          if (k.startsWith('x-') || k === 'content-type') headerObj[k] = v;
        }
        headersView.innerText = JSON.stringify(headerObj, null, 2);

        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({}));
          appendMessage('assistant', `Error ${resp.status}: ${errData.error?.message || 'Gateway error'}`, {
            cacheStatus: 'ERROR',
            provider: provider,
            latency: gatewayLatency,
            requestId: requestId
          });
          payloadView.innerText = JSON.stringify(errData, null, 2);
          return;
        }

        if (isStream) {
          // SSE streaming handler
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let assistantMsg = '';
          const msgEl = appendStreamingMessage();

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\\n');
            for (const line of lines) {
              if (line.startsWith('data: ')) {
                const dataStr = line.slice(6).trim();
                if (dataStr === '[DONE]') continue;
                try {
                  const parsed = JSON.parse(dataStr);
                  const delta = parsed.choices?.[0]?.delta?.content || '';
                  assistantMsg += delta;
                  msgEl.querySelector('.message-bubble').innerText = assistantMsg;
                } catch (e) {}
              }
            }
          }

          finalizeStreamingMessage(msgEl, {
            cacheStatus: cacheStatus,
            provider: provider,
            latency: gatewayLatency,
            requestId: requestId
          });
          payloadView.innerText = "// SSE stream finished";
        } else {
          const data = await resp.json();
          const reply = data.choices?.[0]?.message?.content || '(No response)';
          const promptTokens = data.usage?.prompt_tokens || 0;
          const completionTokens = data.usage?.completion_tokens || 0;
          const similarity = resp.headers.get('x-cache-similarity');

          appendMessage('assistant', reply, {
            cacheStatus: cacheStatus,
            similarity: similarity,
            provider: provider,
            latency: gatewayLatency,
            requestId: requestId,
            promptTokens: promptTokens,
            completionTokens: completionTokens
          });

          payloadView.innerText = JSON.stringify(data, null, 2);

          // Update Inspector Telemetry
          metricLatency.innerText = `${gatewayLatency} ms`;
          metricProvider.innerText = `Upstream: ${provider}`;

          if (cacheStatus === 'HIT-EXACT') {
            metricCacheBadge.innerHTML = `<span style="color:var(--accent-emerald); font-weight:700;">&#9889; HIT-EXACT</span>`;
            const cold = coldCallLatencies[text] || 850;
            const ratio = (cold / Math.max(parseFloat(gatewayLatency), 1)).toFixed(1);
            metricSpeedup.innerText = `${ratio}x`;
          } else if (cacheStatus === 'HIT-SEMANTIC') {
            const simPct = similarity ? ` (${(parseFloat(similarity) * 100).toFixed(1)}%)` : '';
            metricCacheBadge.innerHTML = `<span style="color:var(--accent-cyan); font-weight:700;">&#10024; HIT-SEMANTIC${simPct}</span>`;
            const cold = coldCallLatencies[text] || 850;
            const ratio = (cold / Math.max(parseFloat(gatewayLatency), 1)).toFixed(1);
            metricSpeedup.innerText = `${ratio}x`;
          } else {
            coldCallLatencies[text] = parseFloat(gatewayLatency);
            metricCacheBadge.innerHTML = `<span style="color:var(--accent-amber);">&#127760; MISS (Cold call)</span>`;
            metricSpeedup.innerText = '1.0x (baseline)';
          }
        }
      } catch (err) {
        appendMessage('assistant', `Connection error: ${err.message}`);
      } finally {
        btnSend.disabled = false;
        chatMessages.scrollTop = chatMessages.scrollHeight;
      }
    }

    function appendMessage(role, text, telemetry = null) {
      const row = document.createElement('div');
      row.className = `message-row ${role}`;

      const bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      bubble.innerText = text;
      row.appendChild(bubble);

      if (telemetry && role === 'assistant') {
        const bar = document.createElement('div');
        bar.className = 'telemetry-bar';

        let badgeClass = 'badge-cache-miss';
        let badgeIcon = '&#127760;';
        if (telemetry.cacheStatus === 'HIT-EXACT') {
          badgeClass = 'badge-cache-exact';
          badgeIcon = '&#9889;';
        } else if (telemetry.cacheStatus === 'HIT-SEMANTIC') {
          badgeClass = 'badge-cache-semantic';
          badgeIcon = '&#10024;';
        }

        const simBadge = telemetry.similarity 
          ? `<span class="badge-pill" style="color:var(--accent-cyan); border-color:rgba(6,182,212,0.4);">&#128373; ${(parseFloat(telemetry.similarity)*100).toFixed(1)}% match</span>` 
          : '';

        bar.innerHTML = `
          <span class="${badgeClass}">${badgeIcon} ${telemetry.cacheStatus}</span>
          ${simBadge}
          <span class="badge-pill">&#9201; ${telemetry.latency} ms</span>
          <span class="badge-pill">&#9881; ${telemetry.provider}</span>
          ${telemetry.promptTokens ? `<span class="badge-pill">&#128290; ${telemetry.promptTokens + telemetry.completionTokens} tok</span>` : ''}
          ${telemetry.cacheStatus.startsWith('HIT') ? '<span class="badge-speedup">&#9889; Cached Accelerator</span>' : ''}
        `;
        row.appendChild(bar);
      }

      chatMessages.appendChild(row);
      chatMessages.scrollTop = chatMessages.scrollHeight;
      return row;
    }

    function appendStreamingMessage() {
      const row = document.createElement('div');
      row.className = 'message-row assistant';
      const bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      bubble.innerText = '...';
      row.appendChild(bubble);
      chatMessages.appendChild(row);
      return row;
    }

    function finalizeStreamingMessage(msgEl, telemetry) {
      const bar = document.createElement('div');
      bar.className = 'telemetry-bar';
      bar.innerHTML = `
        <span class="badge-cache-miss">&#127760; STREAMED</span>
        <span class="badge-pill">&#9201; ${telemetry.latency} ms</span>
        <span class="badge-pill">&#9881; ${telemetry.provider}</span>
      `;
      msgEl.appendChild(bar);
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // Dynamic model loader
    async function loadModels() {
      try {
        const resp = await fetch('/v1/models');
        if (resp.ok) {
          const data = await resp.json();
          if (data.data && Array.isArray(data.data)) {
            modelSelect.innerHTML = '';
            data.data.forEach(m => {
              const opt = document.createElement('option');
              opt.value = m.id;
              if (m.id === 'llama3.2' || m.id === 'ollama') {
                opt.innerText = `🦙 ${m.id} (Free Local Ollama)`;
              } else if (m.id === 'deepseek-r1') {
                opt.innerText = `🐋 deepseek-r1 (Ollama Thinking)`;
              } else if (m.id === 'qwen2.5' || m.id === 'gemma2') {
                opt.innerText = `✨ ${m.id} (Free Local Ollama)`;
              } else if (m.id.startsWith('mock')) {
                opt.innerText = `🧪 ${m.id} (Mock Echo)`;
              } else {
                opt.innerText = `⚡ ${m.id} (Cloud / Ollama Fallback)`;
              }
              modelSelect.appendChild(opt);
            });
            if (Array.from(modelSelect.options).some(o => o.value === 'llama3.2')) {
              modelSelect.value = 'llama3.2';
            }
            hintProvider.innerText = modelSelect.value;
          }
        }
      } catch (e) {}
    }

    loadModels();
  </script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
@router.get("/ui/", response_class=HTMLResponse, include_in_schema=False)
async def playground_page() -> HTMLResponse:
    """Serve the CacheGate interactive developer playground."""
    return HTMLResponse(content=PLAYGROUND_HTML, status_code=200)
