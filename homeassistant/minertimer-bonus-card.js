/*
 * minertimer-bonus-card
 * Runder Regler (wie bei der Heizung) für die Bonuszeit von minertimer.
 * Ruft beim Tippen auf "Freigeben" ein Script mit dem Feld `minuten` auf.
 *
 * type: custom:minertimer-bonus-card
 * script: script.minecraft_frederick_bonus           # Pflicht
 * remaining_entity: sensor.<...>_restzeit_heute      # optional, zeigt "Restzeit danach"
 * title: Bonuszeit für Frederick                     # optional
 * min: 0 / max: 60 / step: 5 / default: 30           # optional
 * color: "#4caf50"                                   # optional
 * back_after_send: true                              # optional, nach dem Senden zurück
 * back_path: /zuhause-dashboard/home                 # optional, Ziel dafür (sonst Browser-Zurück)
 */
const VERSION = "1.0.0";
const START = 135;  // Grad, unten links
const SWEEP = 270;  // Grad, bis unten rechts

class MinertimerBonusCard extends HTMLElement {
  setConfig(config) {
    if (!config.script || !config.script.startsWith("script.")) {
      throw new Error("Bitte 'script: script.<name>' angeben");
    }
    this._cfg = {
      title: "Bonuszeit",
      min: 0, max: 60, step: 5, default: 30,
      color: "#4caf50",
      back_after_send: false,
      ...config,
    };
    this._value = this._clamp(this._cfg.default);
    this._busy = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._updateInfo();
  }

  getCardSize() { return 7; }
  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }

  _clamp(v) {
    const { min, max, step } = this._cfg;
    v = Math.round((v - min) / step) * step + min;
    return Math.min(max, Math.max(min, v));
  }

  _render() {
    const c = this._cfg;
    this.shadowRoot.innerHTML = `
      <style>
        :host { --mbc-color: ${c.color}; }
        ha-card { padding: 16px 16px 20px; display: flex; flex-direction: column; align-items: center; }
        .title { align-self: stretch; font-size: 1.15em; font-weight: 500; margin: 0 0 4px 4px; color: var(--primary-text-color); }
        .dial { position: relative; width: 100%; max-width: 300px; aspect-ratio: 1; touch-action: none; user-select: none; -webkit-user-select: none; }
        svg { width: 100%; height: 100%; overflow: visible; }
        .track { stroke: var(--secondary-background-color, rgba(127,127,127,.18)); }
        .fill { stroke: var(--mbc-color); transition: stroke-dashoffset .15s; }
        .dragging .fill, .dragging .handle { transition: none; }
        .handle { fill: #fff; stroke: var(--mbc-color); stroke-width: 7; cursor: grab; filter: drop-shadow(0 1px 3px rgba(0,0,0,.45)); }
        .center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none; }
        .label { color: var(--mbc-color); font-size: 1.05em; font-weight: 500; }
        .value { font-size: 4.6em; line-height: 1.05; font-weight: 400; color: var(--primary-text-color); }
        .value small { font-size: .3em; margin-left: 4px; color: var(--secondary-text-color); }
        .info { font-size: .9em; color: var(--secondary-text-color); min-height: 1.3em; }
        .buttons { display: flex; gap: 28px; margin-top: -34px; z-index: 1; }
        .round { width: 52px; height: 52px; border-radius: 50%; border: 1.5px solid var(--secondary-text-color); background: none;
                 color: var(--primary-text-color); font-size: 28px; line-height: 1; cursor: pointer; display: flex; align-items: center; justify-content: center; }
        .round:disabled { opacity: .3; cursor: default; }
        .send { margin-top: 22px; width: 100%; max-width: 300px; height: 48px; border: none; border-radius: 24px;
                background: var(--mbc-color); color: #fff; font-size: 1.05em; font-weight: 500; cursor: pointer; }
        .send:disabled { opacity: .35; cursor: default; }
        .send.ok { background: var(--success-color, #43a047); }
      </style>
      <ha-card>
        <div class="title">${c.title}</div>
        <div class="dial">
          <svg viewBox="0 0 320 320">
            <path class="track" d="${this._arc(START, START + SWEEP)}" fill="none" stroke-width="26" stroke-linecap="round"/>
            <path class="fill" d="${this._arc(START, START + SWEEP)}" fill="none" stroke-width="26" stroke-linecap="round"/>
            <circle class="handle" r="13"/>
          </svg>
          <div class="center">
            <div class="label">Zusätzlich</div>
            <div class="value"><span class="num"></span><small>min</small></div>
            <div class="info"></div>
          </div>
        </div>
        <div class="buttons">
          <button class="round minus" aria-label="weniger">&minus;</button>
          <button class="round plus" aria-label="mehr">+</button>
        </div>
        <button class="send"></button>
      </ha-card>`;
    const r = this.shadowRoot;
    this._el = {
      dial: r.querySelector(".dial"), svg: r.querySelector("svg"), fill: r.querySelector(".fill"),
      handle: r.querySelector(".handle"), num: r.querySelector(".num"), info: r.querySelector(".info"),
      minus: r.querySelector(".minus"), plus: r.querySelector(".plus"), send: r.querySelector(".send"),
    };
    this._el.minus.addEventListener("click", () => this._set(this._value - c.step));
    this._el.plus.addEventListener("click", () => this._set(this._value + c.step));
    this._el.send.addEventListener("click", () => this._send());
    this._el.svg.addEventListener("pointerdown", (e) => this._drag(e));
    this._update();
  }

  _pt(deg, rad = 130) {
    const a = (deg * Math.PI) / 180;
    return [160 + rad * Math.cos(a), 160 + rad * Math.sin(a)];
  }

  _arc(from, to) {
    const [x1, y1] = this._pt(from), [x2, y2] = this._pt(to);
    return `M ${x1} ${y1} A 130 130 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
  }

  _drag(e) {
    e.preventDefault();
    const svg = this._el.svg;
    svg.setPointerCapture(e.pointerId);
    this._el.dial.classList.add("dragging");
    const move = (ev) => {
      const b = svg.getBoundingClientRect();
      const x = ((ev.clientX - b.left) / b.width) * 320 - 160;
      const y = ((ev.clientY - b.top) / b.height) * 320 - 160;
      let deg = (Math.atan2(y, x) * 180) / Math.PI;      // -180..180, 0 = rechts
      let rel = (deg - START + 720) % 360;                 // 0..360 ab Start
      if (rel > SWEEP) rel = rel - SWEEP < (360 - SWEEP) / 2 ? SWEEP : 0; // Lücke unten
      const { min, max } = this._cfg;
      const v = min + (rel / SWEEP) * (max - min);
      // beim Ziehen nicht über die Lücke unten von 60 auf 0 springen (und umgekehrt)
      if (ev !== e && Math.abs(v - this._value) > (max - min) / 2) return;
      this._set(v);
    };
    const up = () => {
      svg.removeEventListener("pointermove", move);
      svg.removeEventListener("pointerup", up);
      svg.removeEventListener("pointercancel", up);
      this._el.dial.classList.remove("dragging");
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerup", up);
    svg.addEventListener("pointercancel", up);
    move(e);
  }

  _set(v) {
    const nv = this._clamp(v);
    if (nv === this._value) return;
    this._value = nv;
    this._update();
  }

  _update() {
    const { min, max } = this._cfg;
    const frac = (this._value - min) / (max - min);
    const total = (2 * Math.PI * 130 * SWEEP) / 360;
    this._el.fill.style.strokeDasharray = `${total}`;
    this._el.fill.style.strokeDashoffset = `${total * (1 - frac)}`;
    const [hx, hy] = this._pt(START + frac * SWEEP);
    this._el.handle.setAttribute("cx", hx);
    this._el.handle.setAttribute("cy", hy);
    this._el.num.textContent = this._value;
    this._el.minus.disabled = this._value <= min;
    this._el.plus.disabled = this._value >= max;
    if (!this._busy) {
      this._el.send.textContent = this._value > 0 ? `${this._value} min freigeben` : "Minuten wählen";
      this._el.send.disabled = this._value <= 0;
    }
    this._updateInfo();
  }

  _updateInfo() {
    if (!this._el) return;
    const id = this._cfg.remaining_entity;
    const st = id && this._hass ? this._hass.states[id] : null;
    const rest = st ? parseFloat(st.state) : NaN;
    this._el.info.textContent = isNaN(rest)
      ? ""
      : `Restzeit danach: ${Math.round(rest) + this._value} min`;
  }

  async _send() {
    if (!this._hass || this._value <= 0 || this._busy) return;
    this._busy = true;
    const btn = this._el.send;
    btn.disabled = true;
    const minuten = this._value;
    let ok = false;
    try {
      await this._hass.callService("script", this._cfg.script.slice(7), { minuten });
      btn.textContent = `✓ ${minuten} min freigegeben`;
      btn.classList.add("ok");
      ok = true;
    } catch (err) {
      btn.textContent = "Fehler beim Senden";
      console.error("minertimer-bonus-card", err);
    }
    setTimeout(() => {
      this._busy = false;
      btn.classList.remove("ok");
      this._update();
      if (ok && this._cfg.back_after_send) this._goBack();
    }, 1800);
  }

  _goBack() {
    const path = this._cfg.back_path;
    if (!path) { history.back(); return; }
    history.replaceState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: true } }));
  }

  static getStubConfig() {
    return { script: "script.minecraft_frederick_bonus" };
  }
}

if (!customElements.get("minertimer-bonus-card")) {
  customElements.define("minertimer-bonus-card", MinertimerBonusCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "minertimer-bonus-card",
    name: "minertimer Bonuszeit",
    description: "Runder Regler für minertimer-Bonusminuten",
  });
  console.info(`%c minertimer-bonus-card ${VERSION} `, "background:#4caf50;color:#fff");
}
