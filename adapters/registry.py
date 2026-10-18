"""Declared source identities; labels are not authenticated provenance."""

def source_identity(bundle):
    manifest = bundle.get('manifest', {})
    source = manifest.get('source', {})
    source = source if isinstance(source, dict) else {}
    agent = manifest.get('agent', {})
    agent = agent if isinstance(agent, dict) else {}
    explicit = 'adapter' in source
    declared = source.get('adapter') if explicit else str(agent.get('name', '')).lower()
    if explicit:
        adapter = declared if declared in ('artex', 'codex', 'otlp', 'orcareplay') else 'generic'
    else:
        adapter = {'artex': 'artex', 'codex': 'codex', 'opentelemetry': 'otlp', 'orcareplay': 'orcareplay'}.get(declared, 'generic')
    extensions = agent.get('extensions', {})
    extensions = extensions if isinstance(extensions, dict) else {}
    native_format = source.get('format')
    native_version = source.get('format_version')
    if adapter == 'artex':
        native_format = native_format or 'TaskArchiveSnapshot'
        native_version = native_version if native_version is not None else extensions.get('archive_format_version')
    elif adapter == 'codex':
        session_meta = source.get('session_meta', {})
        session_meta = session_meta if isinstance(session_meta, dict) else {}
        native_version = native_version if native_version is not None else session_meta.get('cli_version', agent.get('version'))
    completeness = manifest.get('completeness', {})
    completeness = completeness if isinstance(completeness, dict) else {}
    gaps = completeness.get('gaps', [])
    return {'adapter_id': adapter, 'declared_adapter': declared if isinstance(declared, str) else None,
            'support_tier': {'artex': 'stable', 'codex': 'experimental', 'otlp': 'experimental', 'orcareplay': 'experimental'}.get(adapter, 'future'),
            'native_format': native_format if isinstance(native_format, str) else 'unknown',
            'native_version': native_version if isinstance(native_version, (str, int)) and not isinstance(native_version, bool) else 'unknown',
            'capture_surface': source.get('capture_surface') if isinstance(source.get('capture_surface'), str) else 'unknown',
            'coverage_status': completeness.get('status') if isinstance(completeness.get('status'), str) else 'unknown',
            'coverage_gap_count': len(gaps) if isinstance(gaps, list) else 0,
            'identity_basis': 'manifest.source.adapter' if explicit else 'legacy-agent-name',
            'declared': True}
