(() => {
  "use strict";
  const form = document.getElementById("print-form"), input = document.getElementById("id_document");
  if (!form || !input) return;
  const list = document.getElementById("upload-file-list"), count = document.getElementById("upload-count");
  const note = document.getElementById("shared-settings-note"), submit = document.querySelector("#print-submit span");
  const optionsField = document.getElementById("id_image_options"), manifestField = document.getElementById("id_document_manifest");
  const modalElement = document.getElementById("image-layout-modal");
  const modal = modalElement ? bootstrap.Modal.getOrCreateInstance(modalElement) : null;
  const canvas = modalElement?.querySelector("[data-image-layout-canvas]"), controls = [...(modalElement?.querySelectorAll("[data-image-option]") || [])];
  const defaults = {paper:"a4",orientation:"auto",fit:"contain",margin_mm:10,horizontal:"center",vertical:"center",rotation:0,dpi:300,background:"#ffffff"};
  const papers = {a5:[420,595],a4:[595,842],a3:[842,1191],letter:[612,792],photo_4x6:[288,432]};
  const imageSuffixes = [".png",".jpg",".jpeg",".webp",".tif",".tiff",".bmp",".heic",".heif"];
  const settings = new Map(), urls = new Map(), images = new Map();
  let activeKey = "";
  const keyFor = (file) => `${file.name}::${file.size}::${file.lastModified}`;
  const isImage = (file) => file.type.toLowerCase().startsWith("image/") || imageSuffixes.some((suffix) => file.name.toLowerCase().endsWith(suffix));
  const size = (bytes) => bytes < 1048576 ? `${(bytes/1024).toFixed(1)} KB` : `${(bytes/1048576).toFixed(1)} MB`;

  function syncFields(files) {
    manifestField.value = JSON.stringify(files.map((file) => ({name:file.name,size:file.size,type:isImage(file)?"image":"pdf"})));
    optionsField.value = JSON.stringify(files.filter(isImage).map((file) => ({name:file.name,size:file.size,options:settings.get(keyFor(file))||defaults})));
  }
  function getUrl(file) {
    const key = keyFor(file);
    if (!urls.has(key)) urls.set(key, URL.createObjectURL(file));
    return urls.get(key);
  }
  function loadImage(file) {
    const key = keyFor(file);
    if (images.has(key)) return images.get(key);
    const image = new Image(); image.src = getUrl(file); images.set(key,image); return image;
  }
  function pageLayout(image, raw) {
    const options = {...defaults,...raw}, rotated = Number(options.rotation)%180 !== 0;
    const iw = rotated ? image.naturalHeight : image.naturalWidth, ih = rotated ? image.naturalWidth : image.naturalHeight;
    let [pw,ph] = papers[options.paper] || papers.a4;
    let orientation = options.orientation === "auto" ? (iw>ih?"landscape":"portrait") : options.orientation;
    if ((orientation === "landscape") !== (pw>ph)) [pw,ph] = [ph,pw];
    const margin = Number(options.margin_mm)*72/25.4, aw = Math.max(1,pw-margin*2), ah = Math.max(1,ph-margin*2);
    let dw=aw,dh=ah;
    if (options.fit !== "stretch") {
      let scale;
      if (options.fit === "cover") scale=Math.max(aw/iw,ah/ih);
      else if (options.fit === "original") scale=Math.min(72/Number(options.dpi||300),aw/iw,ah/ih);
      else scale=Math.min(aw/iw,ah/ih);
      dw=iw*scale;dh=ih*scale;
    }
    const x={left:margin,center:(pw-dw)/2,right:pw-margin-dw}[options.horizontal];
    const y={top:margin,center:(ph-dh)/2,bottom:ph-margin-dh}[options.vertical];
    return {options,pw,ph,x,y,dw,dh};
  }
  function paint(target, image, raw) {
    if (!image.complete || !image.naturalWidth) { image.addEventListener("load",()=>paint(target,image,raw),{once:true}); return; }
    const layout=pageLayout(image,raw), maxWidth=target.closest(".image-layout-preview")?700:150, scale=Math.min(maxWidth/layout.pw,220/layout.ph);
    target.width=Math.max(1,Math.round(layout.pw*scale));target.height=Math.max(1,Math.round(layout.ph*scale));
    const ctx=target.getContext("2d");ctx.fillStyle=layout.options.background;ctx.fillRect(0,0,target.width,target.height);ctx.save();
    ctx.beginPath();ctx.rect(0,0,target.width,target.height);ctx.clip();ctx.translate((layout.x+layout.dw/2)*scale,(layout.y+layout.dh/2)*scale);ctx.rotate(Number(layout.options.rotation)*Math.PI/180);
    const rotated=Number(layout.options.rotation)%180!==0, drawWidth=(rotated?layout.dh:layout.dw)*scale,drawHeight=(rotated?layout.dw:layout.dh)*scale;
    ctx.drawImage(image,-drawWidth/2,-drawHeight/2,drawWidth,drawHeight);ctx.restore();
  }
  function updateEditor() {
    const file=[...input.files].find((item)=>keyFor(item)===activeKey);if(!file)return;
    const options={...defaults};controls.forEach((control)=>{options[control.dataset.imageOption]=["margin_mm","rotation","dpi"].includes(control.dataset.imageOption)?Number(control.value):control.value});
    settings.set(activeKey,options);modalElement.querySelector("[data-margin-value]").textContent=`${options.margin_mm} mm`;paint(canvas,loadImage(file),options);syncFields([...input.files]);
  }
  function openEditor(file) {
    activeKey=keyFor(file);const options=settings.get(activeKey)||{...defaults};settings.set(activeKey,options);
    controls.forEach((control)=>{control.value=options[control.dataset.imageOption]});modalElement.querySelector(".modal-title").textContent=file.name;updateEditor();modal.show();
  }
  function removeAt(files,index) {
    const removed=files[index],key=keyFor(removed),transfer=new DataTransfer();files.forEach((file,i)=>{if(i!==index)transfer.items.add(file)});input.files=transfer.files;
    if(urls.has(key))URL.revokeObjectURL(urls.get(key));urls.delete(key);images.delete(key);settings.delete(key);input.dispatchEvent(new Event("change",{bubbles:true}));
  }
  function render() {
    const files=[...input.files];files.filter(isImage).forEach((file)=>{if(!settings.has(keyFor(file)))settings.set(keyFor(file),{...defaults})});syncFields(files);
    list.replaceChildren(...files.map((file,index)=>{const imageDocument=isImage(file),card=document.createElement("article");card.className="print-document-card";
      const thumb=document.createElement("div");thumb.className="print-document-thumb";
      if(imageDocument){const preview=document.createElement("canvas"),fallback=document.createElement("span");fallback.className="print-document-thumb-fallback";fallback.innerHTML='<i class="bi bi-image"></i>';thumb.append(preview,fallback);const image=loadImage(file);image.addEventListener("load",()=>{fallback.hidden=true},{once:true});image.addEventListener("error",()=>{preview.hidden=true;fallback.hidden=false},{once:true});paint(preview,image,settings.get(keyFor(file)));}else{const frame=document.createElement("iframe");frame.src=`${getUrl(file)}#page=1&toolbar=0&navpanes=0&scrollbar=0`;frame.title=`First page of ${file.name}`;thumb.append(frame)}
      const details=document.createElement("div");details.className="print-document-details";const name=document.createElement("div");name.className="print-document-name";name.textContent=file.name;
      const meta=document.createElement("div");meta.className="print-document-meta";meta.innerHTML=`<span>${imageDocument?'Image → PDF':'PDF'}</span><span>${size(file.size)}</span>`;
      const actions=document.createElement("div");actions.className="print-document-actions";
      if(imageDocument){const edit=document.createElement("button");edit.type="button";edit.className="btn btn-sm btn-primary";edit.innerHTML='<i class="bi bi-sliders me-1"></i><span>Edit composition</span>';edit.addEventListener("click",()=>openEditor(file));actions.append(edit)}
      else{const preview=document.createElement("button");preview.type="button";preview.className="btn btn-sm btn-outline-primary pdf-preview-trigger";preview.dataset.pdfPreview=getUrl(file);preview.dataset.pdfDownload=getUrl(file);preview.dataset.pdfName=file.name;preview.innerHTML='<i class="bi bi-eye me-1"></i><span>Preview</span>';actions.append(preview)}
      const remove=document.createElement("button");remove.type="button";remove.className="btn btn-sm btn-outline-secondary";remove.innerHTML='<i class="bi bi-x-lg me-1"></i><span>Remove</span>';remove.addEventListener("click",()=>removeAt(files,index));actions.append(remove);details.append(name,meta,actions);card.append(thumb,details);return card}));
    count.textContent=files.length?`${files.length} document${files.length===1?'':'s'} selected`:'No documents selected';note.hidden=files.length<2;if(submit)submit.textContent=files.length>1?`Queue ${files.length} documents`:'Send to printer';
  }
  controls.forEach((control)=>control.addEventListener("input",updateEditor));modalElement?.querySelector("[data-image-reset]")?.addEventListener("click",()=>{settings.set(activeKey,{...defaults});const file=[...input.files].find((item)=>keyFor(item)===activeKey);if(file)openEditor(file)});
  modalElement?.addEventListener("hidden.bs.modal",render);input.addEventListener("change",render);window.addEventListener("pagehide",()=>urls.forEach(URL.revokeObjectURL));render();
})();
