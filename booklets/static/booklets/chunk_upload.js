(() => {
  "use strict";
  const CHUNK_SIZE = 16 * 1024 * 1024, MAX_RETRIES = 3;
  const isPdf = (file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  const formatBytes = (value) => {
    if (value < 1024) return `${value} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let amount = value / 1024, index = 0;
    while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
    return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[index]}`;
  };

  class ChunkUploadForm {
    constructor(form, endpoint) {
      this.form = form; this.endpoint = endpoint; this.running = false; this.ready = false;
      this.inputs = Array.from(form.querySelectorAll("input[type='file']")).filter((input) => {
        const accept = (input.accept || "").toLowerCase();
        return accept.includes("pdf") || accept.includes(".pdf");
      });
      if (!this.inputs.length) return;
      this.tokenInput = document.createElement("input");
      this.tokenInput.type = "hidden"; this.tokenInput.name = "chunk_upload_tokens"; form.append(this.tokenInput);
      this.buildStatus();
      form.addEventListener("submit", (event) => this.intercept(event), true);
    }
    selectedPdfs() {
      return this.inputs.flatMap((input) => Array.from(input.files || []).filter(isPdf).map((file) => ({input, file})));
    }
    buildStatus() {
      this.status = document.createElement("section");
      this.status.className = "chunk-upload-status"; this.status.hidden = true;
      this.status.setAttribute("aria-live", "polite"); this.status.setAttribute("aria-atomic", "true");
      this.status.innerHTML = `<div class="chunk-upload-heading"><div class="min-w-0"><div class="chunk-upload-file" data-upload-file></div><div class="chunk-upload-meta" data-upload-meta></div></div><span class="badge text-bg-primary" data-upload-percent>0%</span></div><div class="progress chunk-upload-progress" role="progressbar" aria-label="PDF upload progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="progress-bar progress-bar-striped progress-bar-animated" data-upload-bar style="width:0%"></div></div><div class="chunk-upload-actions"><button class="btn btn-sm btn-outline-danger" type="button" data-upload-cancel><i class="bi bi-x-circle me-1"></i>Cancel</button><button class="btn btn-sm btn-outline-primary" type="button" data-upload-retry hidden><i class="bi bi-arrow-clockwise me-1"></i>Retry</button></div>`;
      const anchor = this.inputs[0].closest(".settings-panel,.card-body,.mb-4") || this.inputs[0].parentElement;
      anchor.append(this.status);
      this.fileLabel = this.status.querySelector("[data-upload-file]"); this.meta = this.status.querySelector("[data-upload-meta]");
      this.percent = this.status.querySelector("[data-upload-percent]"); this.progress = this.status.querySelector("[role='progressbar']");
      this.bar = this.status.querySelector("[data-upload-bar]"); this.cancel = this.status.querySelector("[data-upload-cancel]");
      this.retry = this.status.querySelector("[data-upload-retry]");
      this.cancel.addEventListener("click", () => this.controller?.abort()); this.retry.addEventListener("click", () => this.upload());
    }
    intercept(event) {
      const pdfs = this.selectedPdfs();
      if (this.ready || !pdfs.length) return;
      event.preventDefault(); event.stopImmediatePropagation();
      if (this.running || !this.form.reportValidity()) return;
      this.submitter = event.submitter || this.form.querySelector("button[type='submit'],input[type='submit']");
      this.upload();
    }
    async upload() {
      const selected = this.selectedPdfs();
      if (!selected.length || this.running) return;
      this.running = true; this.controller = new AbortController(); this.status.hidden = false;
      this.status.classList.remove("is-error", "is-complete"); this.retry.hidden = true; this.cancel.hidden = false;
      if (this.submitter) this.submitter.disabled = true;
      const total = selected.reduce((sum, item) => sum + item.file.size, 0), started = performance.now();
      let uploaded = 0; const tokens = [];
      try {
        for (let index = 0; index < selected.length; index += 1) {
          const {input, file} = selected[index];
          this.fileLabel.textContent = `Uploading ${file.name} (${index + 1}/${selected.length})`;
          const start = await this.request({action: "start", filename: file.name, size: String(file.size), field: input.name});
          let offset = start.offset || 0;
          while (offset < file.size) {
            const chunk = file.slice(offset, Math.min(offset + CHUNK_SIZE, file.size));
            const result = await this.requestWithRetry({action: "chunk", token: start.token, offset: String(offset), chunk});
            uploaded += result.offset - offset; offset = result.offset; this.showProgress(uploaded, total, started);
          }
          tokens.push({token: start.token, field: input.name});
        }
        this.tokenInput.value = JSON.stringify(tokens); this.ready = true; this.form.dataset.chunkUploadReady = "true";
        this.inputs.forEach((input) => { input.required = false; });
        this.removeUploadedPdfs(); this.running = false;
        this.status.classList.add("is-complete"); this.fileLabel.textContent = `${selected.length} PDF${selected.length === 1 ? "" : "s"} uploaded`;
        this.meta.textContent = `${formatBytes(total)} received. Processing can now begin.`; this.cancel.hidden = true;
        if (this.submitter) this.submitter.disabled = false;
        this.form.requestSubmit(this.submitter || undefined);
      } catch (error) {
        this.running = false; this.status.classList.add("is-error");
        this.fileLabel.textContent = error.name === "AbortError" ? "Upload canceled" : "Upload interrupted";
        this.meta.textContent = error.name === "AbortError" ? "No processing was started." : error.message;
        this.cancel.hidden = true; this.retry.hidden = error.name === "AbortError";
        if (this.submitter) this.submitter.disabled = false;
      }
    }
    removeUploadedPdfs() {
      this.inputs.forEach((input) => {
        const transfer = new DataTransfer();
        Array.from(input.files || []).filter((file) => !isPdf(file)).forEach((file) => transfer.items.add(file));
        input.files = transfer.files;
      });
    }
    showProgress(uploaded, total, started) {
      const value = total ? Math.round(uploaded / total * 100) : 100;
      const seconds = Math.max((performance.now() - started) / 1000, .001);
      this.percent.textContent = `${value}%`; this.bar.style.width = `${value}%`;
      this.progress.setAttribute("aria-valuenow", String(value));
      this.meta.textContent = `${formatBytes(uploaded)} of ${formatBytes(total)} · ${formatBytes(uploaded / seconds)}/s`;
    }
    async requestWithRetry(fields) {
      for (let attempt = 1; attempt <= MAX_RETRIES; attempt += 1) {
        try { return await this.request(fields); }
        catch (error) {
          if (error.name === "AbortError" || attempt === MAX_RETRIES) throw error;
          this.meta.textContent = `Connection interrupted. Retrying block (${attempt}/${MAX_RETRIES})…`;
        }
      }
    }
    async request(fields) {
      const body = new FormData();
      body.append("csrfmiddlewaretoken", this.form.querySelector("input[name='csrfmiddlewaretoken']")?.value || "");
      Object.entries(fields).forEach(([key, value]) => body.append(key, value));
      const response = await fetch(this.endpoint, {method: "POST", body, credentials: "same-origin", signal: this.controller.signal, headers: {Accept: "application/json"}});
      let payload = {};
      try { payload = await response.json(); } catch (_) { /* Proxy error pages are HTML. */ }
      if (!response.ok) throw new Error(payload.error || `Upload failed with HTTP ${response.status}.`);
      return payload;
    }
  }
  const initialize = (root = document) => {
    const endpoint = document.body.dataset.chunkUploadUrl;
    if (!endpoint) return;
    root.querySelectorAll("form[enctype='multipart/form-data']").forEach((form) => {
      if (!form.chunkUpload) form.chunkUpload = new ChunkUploadForm(form, endpoint);
    });
  };
  window.ChunkUpload = {initialize, formatBytes};
  document.addEventListener("DOMContentLoaded", () => initialize());
})();
