/* Signal identification modal — standalone IIFE component.
 * Usage: SignalIdModal.open({ frequency_mhz: 98.5, modulation: 'WFM' })
 *        SignalIdModal.open({})   // blank fields
 *        SignalIdModal.close()
 */
window.SignalIdModal = (function () {
    'use strict';

    var _modal = null;
    var _backdrop = null;
    var _lastFreq = null;
    var _lastMod = null;

    function _esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function _safeSigIdUrl(url) {
        try {
            var p = new URL(String(url || ''));
            if (p.protocol === 'https:' && p.hostname.endsWith('sigidwiki.com')) return p.toString();
        } catch (_) {}
        return null;
    }

    function _build() {
        if (_modal) return;

        _backdrop = document.createElement('div');
        _backdrop.id = 'sigIdBackdrop';
        _backdrop.style.cssText = [
            'position:fixed', 'inset:0', 'z-index:9998',
            'background:rgba(0,0,0,0.65)', 'display:none',
        ].join(';');
        _backdrop.addEventListener('click', close);

        _modal = document.createElement('div');
        _modal.id = 'sigIdModal';
        _modal.style.cssText = [
            'position:fixed', 'top:50%', 'left:50%',
            'transform:translate(-50%,-50%)',
            'z-index:9999',
            'width:min(500px,95vw)', 'max-height:85vh',
            'overflow-y:auto',
            'background:var(--bg-card,#1a1a2e)',
            'border:1px solid rgba(255,255,255,0.12)',
            'border-radius:8px', 'padding:16px',
            'display:none',
            'font-family:var(--font-mono,monospace)',
            'color:var(--text-primary,#e0e0e0)',
            'box-sizing:border-box',
        ].join(';');

        _modal.innerHTML = [
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">',
            '  <div style="font-size:13px;font-weight:700;">Signal Identification</div>',
            '  <button id="sigIdClose" style="background:none;border:none;color:var(--text-muted,#888);',
            '    cursor:pointer;font-size:20px;line-height:1;padding:0 4px;" aria-label="Close">&times;</button>',
            '</div>',
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px;">',
            '  <div>',
            '    <label for="sigIdFreq" style="font-size:10px;color:var(--text-muted,#888);display:block;margin-bottom:3px;">',
            '      Frequency (MHz)</label>',
            '    <input id="sigIdFreq" type="number" step="0.0001" min="0.001" max="6000"',
            '      style="width:100%;background:var(--bg-input,rgba(255,255,255,0.07));',
            '      border:1px solid rgba(255,255,255,0.15);border-radius:4px;',
            '      color:var(--text-primary,#e0e0e0);padding:5px 8px;',
            '      font-family:var(--font-mono,monospace);font-size:11px;box-sizing:border-box;">',
            '  </div>',
            '  <div>',
            '    <label for="sigIdBw" style="font-size:10px;color:var(--text-muted,#888);display:block;margin-bottom:3px;">',
            '      Bandwidth (kHz)</label>',
            '    <input id="sigIdBw" type="number" step="0.1" min="0.1" placeholder="optional"',
            '      style="width:100%;background:var(--bg-input,rgba(255,255,255,0.07));',
            '      border:1px solid rgba(255,255,255,0.15);border-radius:4px;',
            '      color:var(--text-primary,#e0e0e0);padding:5px 8px;',
            '      font-family:var(--font-mono,monospace);font-size:11px;box-sizing:border-box;">',
            '  </div>',
            '</div>',
            '<div style="display:flex;gap:8px;margin-bottom:12px;align-items:flex-end;">',
            '  <div style="flex:1;">',
            '    <label for="sigIdMod" style="font-size:10px;color:var(--text-muted,#888);display:block;margin-bottom:3px;">',
            '      Modulation</label>',
            '    <select id="sigIdMod" style="width:100%;background:var(--bg-input,rgba(255,255,255,0.07));',
            '      border:1px solid rgba(255,255,255,0.15);border-radius:4px;',
            '      color:var(--text-primary,#e0e0e0);padding:5px 8px;',
            '      font-family:var(--font-mono,monospace);font-size:11px;box-sizing:border-box;">',
            '      <option value="">Auto</option>',
            '      <option value="WFM">WFM</option>',
            '      <option value="NFM">NFM</option>',
            '      <option value="FM">FM</option>',
            '      <option value="AM">AM</option>',
            '      <option value="USB">USB</option>',
            '      <option value="LSB">LSB</option>',
            '      <option value="FSK">FSK</option>',
            '      <option value="OOK">OOK</option>',
            '      <option value="PSK">PSK</option>',
            '    </select>',
            '  </div>',
            '  <button id="sigIdSearch" style="background:var(--accent-cyan,#00c8ff);color:#000;',
            '    border:none;border-radius:4px;padding:6px 18px;font-size:11px;font-weight:700;',
            '    cursor:pointer;font-family:var(--font-mono,monospace);white-space:nowrap;">',
            '    Search</button>',
            '</div>',
            '<div id="sigIdStatus" style="font-size:10px;color:var(--text-muted,#888);min-height:14px;margin-bottom:8px;"></div>',
            '<div id="sigIdResults"></div>',
        ].join('');

        document.body.appendChild(_backdrop);
        document.body.appendChild(_modal);

        document.getElementById('sigIdClose').addEventListener('click', close);
        document.getElementById('sigIdSearch').addEventListener('click', search);
        document.getElementById('sigIdFreq').addEventListener('input', _validateFreq);
    }

    function _validateFreq() {
        var freq = document.getElementById('sigIdFreq');
        var btn = document.getElementById('sigIdSearch');
        if (!freq || !btn) return;
        var val = parseFloat(freq.value);
        var valid = isFinite(val) && val > 0;
        btn.disabled = !valid;
        freq.style.borderColor = (freq.value === '' || valid)
            ? 'rgba(255,255,255,0.15)'
            : 'var(--accent-red,#ff4444)';
    }

    function _setStatus(text, isError) {
        var el = document.getElementById('sigIdStatus');
        if (!el) return;
        el.textContent = text || '';
        el.style.color = isError ? 'var(--accent-red,#ff4444)' : 'var(--text-muted,#888)';
    }

    function _renderResults(matches, freqMhz) {
        var el = document.getElementById('sigIdResults');
        if (!el) return;
        if (!matches.length) {
            el.innerHTML = '<div style="font-size:10px;color:var(--text-muted,#888);">'
                + 'No signals match ' + freqMhz.toFixed(4) + ' MHz'
                + ' — try adjusting the frequency or leaving bandwidth blank.</div>';
            return;
        }
        el.innerHTML = matches.map(function (m, i) {
            var dot = i === 0 ? '●' : '○';
            var score = Number(m.score) || 0;
            var name = _esc(m.name || 'Unknown');
            var desc = _esc(m.description || '');
            var cats = Array.isArray(m.categories) ? m.categories : [];
            var reasons = Array.isArray(m.match_reasons) ? m.match_reasons : [];
            var safeUrl = _safeSigIdUrl(m.sigidwiki_url);
            var ranges = Array.isArray(m.frequency_ranges) ? m.frequency_ranges : [];
            var mods = Array.isArray(m.modulations) ? m.modulations : [];
            var bw = m.bandwidth_range;

            var freqStr = ranges.length
                ? (ranges[0].min_hz / 1e6).toFixed(3) + '–' + (ranges[0].max_hz / 1e6).toFixed(3) + ' MHz'
                : '';
            var bwStr = bw
                ? (bw.min_hz >= 1000 ? (bw.min_hz / 1000).toFixed(0) + 'k' : bw.min_hz)
                  + '–'
                  + (bw.max_hz >= 1000 ? (bw.max_hz / 1000).toFixed(0) + 'k' : bw.max_hz)
                  + ' Hz'
                : '';
            var modStr = mods.join(', ');
            var meta = [freqStr, modStr, bwStr].filter(Boolean).join(' · ');

            var catHtml = cats.slice(0, 5).map(function (c) {
                return '<span style="background:rgba(0,200,255,0.1);color:var(--accent-cyan,#00c8ff);'
                    + 'padding:1px 5px;border-radius:3px;font-size:9px;margin-right:3px;">'
                    + _esc(c) + '</span>';
            }).join('');

            return '<div style="border:1px solid rgba(255,255,255,0.08);border-radius:5px;'
                + 'padding:8px;margin-bottom:6px;">'
                + '<div style="display:flex;justify-content:space-between;align-items:flex-start;'
                + 'gap:8px;margin-bottom:4px;">'
                + '<div style="font-size:11px;font-weight:700;">' + dot + ' ' + name + '</div>'
                + '<div style="display:flex;align-items:center;gap:4px;flex-shrink:0;">'
                + '<div style="width:56px;height:5px;background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden;">'
                + '<div style="width:' + score + '%;height:100%;background:var(--accent-cyan,#00c8ff);"></div>'
                + '</div><div style="font-size:9px;color:var(--text-muted,#888);">' + score + '</div>'
                + '</div></div>'
                + (meta ? '<div style="font-size:9px;color:var(--text-muted,#888);margin-bottom:4px;">' + _esc(meta) + '</div>' : '')
                + (desc ? '<div style="font-size:10px;color:var(--text-secondary,#b0b0b0);line-height:1.4;margin-bottom:5px;">' + desc + '</div>' : '')
                + (catHtml ? '<div style="margin-bottom:5px;">' + catHtml + '</div>' : '')
                + (reasons.length ? '<div style="font-size:9px;color:var(--text-dim,#666);">'
                    + reasons.map(_esc).join(' · ') + '</div>' : '')
                + (safeUrl ? '<div style="margin-top:5px;"><a href="' + _esc(safeUrl) + '"'
                    + ' target="_blank" rel="noopener noreferrer"'
                    + ' style="font-size:9px;color:var(--accent-cyan,#00c8ff);text-decoration:none;">'
                    + '↗ View on SigID Wiki</a></div>' : '')
                + '</div>';
        }).join('');
    }

    function open(opts) {
        _build();
        opts = opts || {};

        var freqEl = document.getElementById('sigIdFreq');
        var modEl = document.getElementById('sigIdMod');
        var bwEl = document.getElementById('sigIdBw');
        var resultsEl = document.getElementById('sigIdResults');

        if (opts.frequency_mhz != null) {
            freqEl.value = Number(opts.frequency_mhz).toFixed(4);
            _lastFreq = Number(opts.frequency_mhz);
        } else {
            freqEl.value = '';
            _lastFreq = null;
        }

        var modVal = (opts.modulation || '').toUpperCase();
        modEl.value = modVal || '';

        bwEl.value = '';
        resultsEl.innerHTML = '';
        _setStatus('');
        _validateFreq();

        _backdrop.style.display = 'block';
        _modal.style.display = 'block';

        if (!opts.frequency_mhz) {
            setTimeout(function () { freqEl.focus(); }, 50);
        }
    }

    function close() {
        if (!_modal) return;
        _modal.style.display = 'none';
        _backdrop.style.display = 'none';
    }

    function search() {
        var freqEl = document.getElementById('sigIdFreq');
        var modEl = document.getElementById('sigIdMod');
        var bwEl = document.getElementById('sigIdBw');
        var searchBtn = document.getElementById('sigIdSearch');

        var freq = parseFloat(freqEl.value);
        if (!isFinite(freq) || freq <= 0) return;

        var bwKhz = parseFloat(bwEl.value);
        var bwHz = (isFinite(bwKhz) && bwKhz > 0) ? Math.round(bwKhz * 1000) : null;
        var mod = (modEl.value || '').trim().toUpperCase() || null;

        _setStatus('Searching ' + freq.toFixed(4) + ' MHz…');
        document.getElementById('sigIdResults').innerHTML = '';
        searchBtn.disabled = true;

        fetch('/signalid/match', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                frequency_mhz: freq,
                bandwidth_hz: bwHz,
                modulation: mod,
                limit: 8,
            }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            searchBtn.disabled = false;
            if (!data || data.status !== 'ok') {
                _setStatus('Search failed', true);
                return;
            }
            var matches = data.matches || [];
            if (matches.length) {
                _setStatus(matches.length + ' match' + (matches.length !== 1 ? 'es' : '')
                    + ' for ' + freq.toFixed(4) + ' MHz');
            }
            _renderResults(matches, freq);
        })
        .catch(function () {
            searchBtn.disabled = false;
            _setStatus('Search failed — check connection', true);
            document.getElementById('sigIdResults').innerHTML =
                '<div style="font-size:10px;color:var(--accent-red,#ff4444);">'
                + 'Network error — <button onclick="window.SignalIdModal.search()" '
                + 'style="background:none;border:none;color:var(--accent-cyan,#00c8ff);'
                + 'cursor:pointer;font-size:10px;padding:0;">Retry</button></div>';
        });
    }

    return {open: open, close: close, search: search};
})();
