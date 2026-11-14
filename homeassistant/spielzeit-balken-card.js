/*
 * spielzeit-balken-card
 * Tagesbalken (Maximum pro Tag) aus der Langzeitstatistik, freie Zeitraumwahl,
 * heutiger Tag mit Live-Wert in anderer Farbe, Wochenenden gruen, nur ganze Zahlen.
 * Zeichnet in echter Pixelbreite (Schrift bleibt auf dem Handy gut lesbar).
 */
(() => {
  const pad = n => String(n).padStart(2, '0');
  const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const parse = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const dm = d => `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.`;
  const dmy = d => `${dm(d)}${d.getFullYear()}`;
  const WD = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];
  const isWeekend = d => d.getDay() === 0 || d.getDay() === 6;
  const PRESETS = [['7 Tage', 7], ['14 Tage', 14], ['30 Tage', 30], ['90 Tage', 90], ['1 Jahr', 365], ['2 Jahre', 730]];
  const MAX_DAYS = 730;
  const FS_AXIS = 13;   // Schriftgroesse Y-Achse und Tageswerte
  const FS_X = 13;      // Schriftgroesse X-Achse
  const niceStep = v => {
    v = Math.max(v, 1);
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / p;
    const nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return Math.max(1, nf * p);
  };
  const hm = min => {
    const m = Math.round(min);
    return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
  };

  class SpielzeitBalkenCard extends HTMLElement {
    setConfig(c) {
      if (!c.entity) throw new Error('entity fehlt');
      this._cfg = c;
      const t = new Date();
      const today = new Date(t.getFullYear(), t.getMonth(), t.getDate());
      this._to = today;
      this._from = addDays(today, -((c.default_days || 7) - 1));
      this._map = {};
      this._tok = 0;
      this._built = false;
      this._loaded = false;
      this._lastState = null;
      this._lastW = 0;
    }

    set hass(h) {
      this._hass = h;
      if (!this._built) this._build();
      if (!this._loaded) {
        this._loaded = true;
        this._load();
        return;
      }
      const st = h.states[this._cfg.entity];
      const s = st ? st.state : null;
      if (s !== this._lastState) {
        this._lastState = s;
        this._draw();
      }
    }

    getCardSize() { return 6; }

    disconnectedCallback() {
      if (this._ro) this._ro.disconnect();
    }

    _build() {
      this._built = true;
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.innerHTML = `
        <style>
          ha-card { padding: 16px; }
          .title { font-size: 16px; font-weight: 500; color: var(--secondary-text-color); margin-bottom: 12px; }
          .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
          .chip { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color);
                  border-radius: 16px; padding: 5px 12px; font-size: 13px; cursor: pointer; font-family: inherit; }
          .chip.active { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
          .dates { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; align-items: center; }
          .dates label { font-size: 13px; color: var(--secondary-text-color); display: flex; align-items: center; gap: 6px; }
          .dates input { background: var(--card-background-color); color: var(--primary-text-color);
                         border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px 8px; font-size: 14px;
                         font-family: inherit; color-scheme: dark light; }
          .sum { font-size: 14px; color: var(--secondary-text-color); margin-top: 8px; }
          .legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; color: var(--secondary-text-color); margin-top: 8px; }
          .legend span::before { content: ''; display: inline-block; width: 10px; height: 10px; border-radius: 2px;
                                 margin-right: 6px; background: var(--c); vertical-align: -1px; }
          .msg { padding: 40px 0; text-align: center; color: var(--secondary-text-color); }
          .chart { width: 100%; }
          svg { width: 100%; height: auto; display: block; }
          svg text { fill: var(--secondary-text-color); font-family: inherit; }
          svg text.val { fill: var(--primary-text-color); font-weight: 500; }
        </style>
        <ha-card>
          <div class="title"></div>
          <div class="chips"></div>
          <div class="dates">
            <label>Von <input type="date" id="from"></label>
            <label>Bis <input type="date" id="to"></label>
          </div>
          <div class="chart"></div>
          <div class="legend"></div>
          <div class="sum"></div>
        </ha-card>`;
      const r = this.shadowRoot;
      r.querySelector('.title').textContent = this._cfg.title || 'Spielzeit pro Tag';
      const chips = r.querySelector('.chips');
      PRESETS.forEach(([label, days]) => {
        const b = document.createElement('button');
        b.className = 'chip';
        b.textContent = label;
        b.dataset.days = days;
        b.addEventListener('click', () => {
          const t = new Date();
          this._to = new Date(t.getFullYear(), t.getMonth(), t.getDate());
          this._from = addDays(this._to, -(days - 1));
          this._load();
        });
        chips.appendChild(b);
      });
      const onDate = () => {
        const f = r.getElementById('from').value;
        const t = r.getElementById('to').value;
        if (!f || !t) return;
        let a = parse(f), b = parse(t);
        if (a > b) [a, b] = [b, a];
        const t0 = new Date();
        const today = new Date(t0.getFullYear(), t0.getMonth(), t0.getDate());
        if (b > today) b = today;
        const min = addDays(today, -(MAX_DAYS - 1));
        if (a < min) a = min;
        if (a > b) a = b;
        this._from = a;
        this._to = b;
        this._load();
      };
      r.getElementById('from').addEventListener('change', onDate);
      r.getElementById('to').addEventListener('change', onDate);

      // Bei Breitenaenderung (Drehen, Fenstergroesse) neu zeichnen
      if (window.ResizeObserver) {
        this._ro = new ResizeObserver(() => {
          const w = r.querySelector('.chart').clientWidth;
          if (w && w !== this._lastW && this._loaded && this._hasData) {
            this._draw();
          }
        });
        this._ro.observe(r.querySelector('.chart'));
      }
    }

    _msg(t) {
      this._hasData = false;
      this.shadowRoot.querySelector('.chart').innerHTML = `<div class="msg">${t}</div>`;
      this.shadowRoot.querySelector('.sum').textContent = '';
      this.shadowRoot.querySelector('.legend').innerHTML = '';
    }

    async _load() {
      const tok = ++this._tok;
      const from = this._from, to = this._to;
      this._syncControls();
      this._msg('Lade …');
      try {
        const res = await this._hass.callWS({
          type: 'recorder/statistics_during_period',
          start_time: from.toISOString(),
          end_time: addDays(to, 1).toISOString(),
          statistic_ids: [this._cfg.entity],
          period: 'day',
          types: ['max'],
        });
        if (tok !== this._tok) return;
        const map = {};
        (res[this._cfg.entity] || []).forEach(r => { map[ymd(new Date(r.start))] = r.max; });
        this._map = map;
        this._draw();
      } catch (e) {
        if (tok === this._tok) this._msg('Fehler beim Laden: ' + (e.message || e));
      }
    }

    _syncControls() {
      const r = this.shadowRoot;
      const t0 = new Date();
      const today = new Date(t0.getFullYear(), t0.getMonth(), t0.getDate());
      const n = Math.round((this._to - this._from) / 86400000) + 1;
      r.getElementById('from').value = ymd(this._from);
      r.getElementById('to').value = ymd(this._to);
      r.getElementById('from').min = ymd(addDays(today, -(MAX_DAYS - 1)));
      r.getElementById('from').max = ymd(today);
      r.getElementById('to').max = ymd(today);
      const isToday = ymd(this._to) === ymd(today);
      r.querySelectorAll('.chip').forEach(c => {
        c.classList.toggle('active', isToday && Number(c.dataset.days) === n);
      });
    }

    _draw() {
      if (!this._built) return;
      const r = this.shadowRoot;
      const chartEl = r.querySelector('.chart');
      const t0 = new Date();
      const todayKey = ymd(new Date(t0.getFullYear(), t0.getMonth(), t0.getDate()));
      const st = this._hass && this._hass.states[this._cfg.entity];
      const live = st && st.state !== 'unavailable' && st.state !== 'unknown' ? Number(st.state) : NaN;

      const color = this._cfg.color || '#4c6ed7';
      const todayColor = this._cfg.today_color || '#ff9800';
      const weekendColor = this._cfg.weekend_color || '#43a047';

      // Tageswerte
      const days = [];
      for (let d = new Date(this._from); d <= this._to; d = addDays(d, 1)) {
        const key = ymd(d);
        let v = this._map[key];
        if (key === todayKey && !isNaN(live)) v = live;
        days.push({ date: d, key, v: v == null ? null : Number(v) });
      }
      const n = days.length;
      const total = days.reduce((s, x) => s + (x.v || 0), 0);

      // Balken (ab 100 Tagen Wochen-Durchschnitt pro Tag, dann ohne Wochenend-Farbe)
      let bars;
      let weekly = false;
      if (n > 100) {
        weekly = true;
        bars = [];
        for (let i = 0; i < n; i += 7) {
          const chunk = days.slice(i, i + 7);
          const sum = chunk.reduce((s, x) => s + (x.v || 0), 0);
          bars.push({
            v: sum / chunk.length,
            label: dm(chunk[0].date),
            tip: `${dm(chunk[0].date)}–${dm(chunk[chunk.length - 1].date)}: Ø ${Math.round(sum / chunk.length)} min/Tag`,
            today: chunk.some(x => x.key === todayKey),
            weekend: false,
          });
        }
      } else {
        bars = days.map(x => ({
          v: x.v,
          label: dm(x.date),
          tip: `${WD[x.date.getDay()]} ${dmy(x.date)}: ${x.v == null ? 'keine Daten' : Math.round(x.v) + ' min'}`,
          today: x.key === todayKey,
          weekend: isWeekend(x.date),
        }));
      }
      const fillOf = b => (b.today ? todayColor : b.weekend ? weekendColor : color);

      // Zeichenflaeche in echter Pixelbreite
      const cw = chartEl.clientWidth || 340;
      this._lastW = cw;
      const W = Math.max(260, Math.round(cw));
      const H = Math.round(Math.min(380, Math.max(250, W * 0.72)));
      const L = 46, R = 6, T = 26, B = 32;
      const pw = W - L - R, ph = H - T - B;
      const maxV = Math.max(0, ...bars.map(b => b.v || 0));
      const step = niceStep(maxV / 4 || 1);
      const top = Math.max(step * Math.ceil(maxV / step), step);
      const y = v => T + ph - (v / top) * ph;
      const slot = pw / bars.length;
      const bw = Math.max(1, slot * 0.72);
      let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
      for (let g = 0; g <= top; g += step) {
        const yy = y(g);
        s += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="var(--divider-color)" stroke-width="1" ${g ? 'stroke-dasharray="3 3"' : ''}/>`;
        s += `<text x="${L - 6}" y="${yy + 5}" text-anchor="end" font-size="${FS_AXIS}">${Math.round(g)}</text>`;
      }
      s += `<text x="2" y="14" font-size="${FS_AXIS}">min</text>`;
      const maxLabels = Math.max(2, Math.floor(pw / 46));
      const k = Math.max(1, Math.ceil(bars.length / maxLabels));
      const showVals = bars.length <= 31 && slot >= 24;
      bars.forEach((b, i) => {
        const x = L + i * slot + (slot - bw) / 2;
        const cx = x + bw / 2;
        if (b.v != null && b.v > 0) {
          const yy = y(b.v);
          s += `<rect x="${x}" y="${yy}" width="${bw}" height="${y(0) - yy}" rx="${bw > 6 ? 2 : 0}" fill="${fillOf(b)}"><title>${b.tip}</title></rect>`;
          if (showVals) {
            s += `<text class="val" x="${cx}" y="${yy - 5}" text-anchor="middle" font-size="${FS_AXIS}">${Math.round(b.v)}</text>`;
          }
        } else {
          s += `<rect x="${x}" y="${T}" width="${bw}" height="${ph}" fill="transparent"><title>${b.tip}</title></rect>`;
        }
        if (i % k === 0) {
          s += `<text x="${cx}" y="${H - 9}" text-anchor="middle" font-size="${FS_X}">${b.label}</text>`;
        }
      });
      s += '</svg>';
      chartEl.innerHTML = s;
      this._hasData = true;

      // Legende
      r.querySelector('.legend').innerHTML = weekly
        ? `<span style="--c:${color}">Wochen-Ø pro Tag</span><span style="--c:${todayColor}">aktuelle Woche</span>`
        : `<span style="--c:${color}">Werktag</span><span style="--c:${weekendColor}">Wochenende</span><span style="--c:${todayColor}">Heute</span>`;

      const avg = total / n;
      r.querySelector('.sum').textContent =
        `${dmy(this._from)} – ${dmy(this._to)} · Summe ${hm(total)} · Ø ${Math.round(avg)} min/Tag`;
    }
  }

  if (!customElements.get('spielzeit-balken-card')) {
    customElements.define('spielzeit-balken-card', SpielzeitBalkenCard);
  }
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: 'spielzeit-balken-card',
    name: 'Spielzeit Balken',
    description: 'Tagesbalken aus der Langzeitstatistik mit freier Zeitraumwahl',
  });
})();
