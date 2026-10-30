#!/usr/bin/env python3
"""Patch v0.7.1 NSPanel Flutter source tree with conditional swipe-page visibility.
Run: python3 apply_seasonal_visibility.py /path/to/nspanelpro-flutter
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) == 2 else None
if root is None:
    sys.exit('Usage: python3 apply_seasonal_visibility.py /path/to/nspanelpro-flutter')
dashboard = root/'lib/config/dashboard.dart'
app = root/'lib/app.dart'
if not dashboard.is_file() or not app.is_file():
    sys.exit('Error: directory must contain lib/config/dashboard.dart and lib/app.dart')
a = app.read_text(); d = dashboard.read_text()
if 'visibilityEntitiesFromLovelace' in d or '_visibilityWatchers' in a:
    sys.exit('Patch already installed (or partly installed). Nothing changed.')

def replace_once(src, old, new, path):
    n=src.count(old)
    if n!=1:
        sys.exit(f'Unexpected {path} version: expected anchor once, found {n}. No files modified.\nAnchor: {old[:100]!r}')
    return src.replace(old,new,1)

# Evaluator intentionally treats unknown/unavailable states as not visible for controlled pages.
d=replace_once(d,
'List<PanelPage> pagesFromLovelace(Map<String, dynamic> config) {',
'''/// IDs whose state determines visibility of whole swipe pages.
Set<String> visibilityEntitiesFromLovelace(Map<String, dynamic> config) {
  final ids = <String>{};
  for (final view in (config['views'] as List? ?? const [])) {
    if (view is! Map) continue;
    for (final swipe in _cardsOf(view)) {
      if (!cardType(swipe).endsWith('swipe-card')) continue;
      for (final child in swipe.maps('cards')) {
        for (final rule in child.listOr('visibility')) {
          if (rule is Map && rule['condition'] == 'state') {
            final id = rule['entity']?.toString();
            if (id != null && id.isNotEmpty) ids.add(id);
          }
        }
      }
    }
  }
  return ids;
}

/// A state condition requires an explicit matching HA state.
bool _pageVisible(CardConfig page, String? Function(String) stateOf) {
  final rules = page.listOr('visibility');
  if (rules.isEmpty) return true;
  for (final rule in rules) {
    if (rule is! Map || rule['condition'] != 'state') return false;
    final id = rule['entity']?.toString();
    final expected = rule['state']?.toString();
    if (id == null || expected == null || stateOf(id) != expected) return false;
  }
  return true;
}

List<PanelPage> pagesFromLovelace(
  Map<String, dynamic> config, {
  String? Function(String) stateOf = _unknownState,
}) {''','dashboard.dart')
d=replace_once(d,
'        pages.add(PanelPage(_flatten(child)));',
'        if (_pageVisible(child, stateOf)) pages.add(PanelPage(_flatten(child)));','dashboard.dart')
d=replace_once(d,'/// IDs whose state determines visibility',
'''String? _unknownState(String entityId) => null;

/// IDs whose state determines visibility''','dashboard.dart')

# Existing HA state notifiers are per-entity; use listeners rather than polling.
a=replace_once(a,
'  List<PanelPage> _pages = const [];',
'''  List<PanelPage> _pages = const [];
  Map<String, dynamic>? _visibilityConfig;
  final Map<String, VoidCallback> _visibilityWatchers = {};
  String? _visibilitySignature;

  void _clearVisibilityWatchers() {
    for (final entry in _visibilityWatchers.entries) {
      _states.listen(entry.key).removeListener(entry.value);
    }
    _visibilityWatchers.clear();
  }

  void _configureVisibility(Map<String, dynamic> config) {
    _visibilityConfig = config;
    final wanted = visibilityEntitiesFromLovelace(config);
    for (final id in _visibilityWatchers.keys.toList()) {
      if (!wanted.contains(id)) {
        _states.listen(id).removeListener(_visibilityWatchers.remove(id)!);
      }
    }
    for (final id in wanted) {
      if (_visibilityWatchers.containsKey(id)) continue;
      void onChange() => _refreshVisiblePages();
      _visibilityWatchers[id] = onChange;
      _states.listen(id).addListener(onChange);
    }
    _refreshVisiblePages();
  }

  void _refreshVisiblePages() {
    final config = _visibilityConfig;
    if (config == null || !mounted) return;
    final pages = pagesFromLovelace(config, stateOf: (id) => _states.get(id)?.state);
    final signature = pages.map((p) => p.sourceIndex).join(',');
    if (signature == _visibilitySignature) return;
    _visibilitySignature = signature;
    setState(() {
      _pages = pages;
      _shown = 0;
      _shownPage.value = 0;
      _pager = pagerOptionsFromLovelace(config);
      _scheduleWarmup();
    });
  }''','app.dart')
# initial cache use state query and configure after connection initialized; avoid setState during init
# app's _conn created first; replace cached population with reference and watchers later
# retain ordinary _pages initialization; store config for watcher setup before start

a=replace_once(a,
'        _pages = pagesFromLovelace(cfg);',
'''        _visibilityConfig = cfg;
        _pages = pagesFromLovelace(cfg, stateOf: (id) => _states.get(id)?.state);
        _visibilitySignature = _pages.map((p) => p.sourceIndex).join(',');''','app.dart')
a=replace_once(a,
'    _conn.onReady = _loadConfig;',
'''    if (_visibilityConfig != null) {
      final cachedConfig = _visibilityConfig!;
      // Listener registration in initState is safe; state updates arrive later.
      final ids = visibilityEntitiesFromLovelace(cachedConfig);
      for (final id in ids) {
        void onChange() => _refreshVisiblePages();
        _visibilityWatchers[id] = onChange;
        _states.listen(id).addListener(onChange);
      }
    }
    _conn.onReady = _loadConfig;''','app.dart')
a=replace_once(a,
'    _conn.status.removeListener(_onStatus);',
'    _clearVisibilityWatchers();\n    _conn.status.removeListener(_onStatus);','app.dart')
a=replace_once(a,
'      final pages = pagesFromLovelace(cfg);',
'      final pages = pagesFromLovelace(cfg, stateOf: (id) => _states.get(id)?.state);','app.dart')
a=replace_once(a,
'''      await widget.settings.cacheConfig(jsonEncode(cfg));''',
'''      _configureVisibility(cfg);
      await widget.settings.cacheConfig(jsonEncode(cfg));''','app.dart')
# Page count change must recreate pager and reset index to avoid stale PageController index.
a=replace_once(a,
"key: ValueKey('pager|${_pager.dots}|${_pager.start}'),",
"key: ValueKey('pager|${_pager.dots}|${_pager.start}|${_visibilitySignature ?? ''}'),",'app.dart')

# panel page sourceIndex ensures not rebuilding pager due to minor content; IDs stable across visibility changes
d=replace_once(d,
'''  const PanelPage(this.cards);
  final List<CardConfig> cards;''',
'''  const PanelPage(this.cards, {this.sourceIndex = 0});
  final List<CardConfig> cards;
  final int sourceIndex;''','dashboard.dart')
d=replace_once(d,
'''      for (final child in swipe.maps('cards')) {
        if (_pageVisible(child, stateOf)) pages.add(PanelPage(_flatten(child)));
      }''',
'''      final children = swipe.maps('cards');
      for (var i = 0; i < children.length; i++) {
        final child = children[i];
        if (_pageVisible(child, stateOf)) {
          pages.add(PanelPage(_flatten(child), sourceIndex: i));
        }
      }''','dashboard.dart')
# Replace two files only after all anchors verified
for p, content in ((dashboard,d),(app,a)):
    p.write_text(content)
print('SUCCESS: patched lib/config/dashboard.dart and lib/app.dart')
print('Next: run flutter analyze and flutter test, then build APK on GitHub Actions.')
