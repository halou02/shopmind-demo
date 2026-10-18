const state = {
  materialPath: "",
  analysis: null,
  publish: null,
  selectedDirectionId: null,
  demoPacks: [],
  materialItems: [],
  materialImages: [],
  selectedReferenceImages: [],
  imagesConfirmed: false,
  materialsReady: false,
  pickerDraftPaths: null,
  materialStats: null,
  materialFolders: [],
  activeMaterialFolderId: null,
  materialFolderAddTargetId: null,
  materialFolderDeleteTargetId: null,
  products: [],
  projects: [],
  help: null,
  materialCategory: "all",
  materialKeyword: "",
  productCategory: "all",
  productKeyword: "",
  understandingNotes: "",
  productForm: {},
  imagePrompts: [],
  generatedMedia: {},
  generateImage: false,
  generateVideo: false,
  historyItems: [],
  imagePickerMode: "step1",
  libraryPickerDraftPaths: null,
  libraryPickerFolderId: null,
  libraryPickerMode: "all",
  libraryPath: "",
  referenceFiles: [],
  addProductPendingFiles: [],
  creativeTaskCompleted: false,
  step5ExportSnapshot: null,
  productDetail: {
    productId: null,
    name: "",
    source: "workspace",
    editing: false,
    loading: false,
    profile: {},
    sections: [],
    files: [],
  },
};

const LIBRARY_PROTECTED_FILENAMES = new Set(["library_index.json", "商品资料.xlsx"]);

function libraryMaterialItemName(item) {
  if (!item) return "";
  if (item.name) return item.name;
  const path = String(item.relative_path || "");
  return path.split("/").pop() || path;
}

function libraryMaterialItemsForDisplay() {
  return (state.materialItems || []).filter(
    (item) => !LIBRARY_PROTECTED_FILENAMES.has(libraryMaterialItemName(item))
  );
}

function materialCanDeleteFromLibrary(item) {
  if (!item?.relative_path) return false;
  const name = item.name || item.relative_path.split("/").pop();
  return !LIBRARY_PROTECTED_FILENAMES.has(name);
}

function isHiddenSystemReferenceFile(item) {
  return LIBRARY_PROTECTED_FILENAMES.has(libraryMaterialItemName(item));
}

function referenceFilesForDisplay() {
  return (state.referenceFiles || []).filter((item) => !isHiddenSystemReferenceFile(item));
}

function showProductDetailModal() {
  const modal = document.getElementById("productDetailModal");
  if (!modal) return;
  modal.classList.add("active");
  modal.setAttribute("aria-hidden", "false");
}

function hideProductDetailModal() {
  const modal = document.getElementById("productDetailModal");
  if (!modal) return;
  modal.classList.remove("active");
  modal.setAttribute("aria-hidden", "true");
}

function isPlaceholderProductName(value) {
  const text = String(value ?? "").trim();
  return !text || text === PLACEHOLDER_PRODUCT_NAME;
}

const CHANNEL_LABELS = {
  xiaohongshu: "小红书",
  douyin: "抖音",
  kuaishou: "快手",
  shopping: "购物平台",
  visual: "视觉方案",
  video: "视频脚本",
};

const COPY_PLATFORM_ORDER = ["xiaohongshu", "douyin", "kuaishou", "shopping"];

const CATEGORY_LABELS = {
  image: "图片",
  video: "视频",
  text: "文本",
  copy: "文本",
  brand: "文本",
  reference: "文本",
};

function normalizeLibraryPath(path) {
  return String(path || "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

function readDataFolderId(element) {
  if (!element) return "";
  return element.getAttribute("data-folder-id") || element.dataset.folderId || "";
}

function normalizeMaterialCategory(category) {
  const value = String(category || "").toLowerCase();
  if (value === "copy" || value === "brand" || value === "reference") return "text";
  if (value === "image" || value === "video" || value === "text") return value;
  return "text";
}

function materialFileIcon(item) {
  const category = normalizeMaterialCategory(item.category);
  if (category === "video") return "🎬";
  const name = (item.name || item.relative_path || "").toLowerCase();
  if (/\.(xlsx?|csv)$/.test(name)) return "📊";
  if (/\.pdf$/.test(name)) return "📕";
  if (/\.(docx?|md|txt)$/.test(name)) return "📝";
  return "📄";
}

function renderMaterialPickerThumb(item) {
  if (item.is_image) {
    return `<img src="${item.url}" alt="${escapeHtml(item.name)}" loading="lazy">`;
  }
  const icon = item.category === "video" ? "🎬" : materialFileIcon(item);
  return `<div class="material-doc-thumb" aria-hidden="true">${icon}</div>`;
}

function renderMaterialPickerCard(item, options = {}) {
  const {
    checked = false,
    selected = false,
    mode = "checkbox",
    dataPath = item.relative_path,
  } = options;
  const category = normalizeMaterialCategory(item.category);
  const label = CATEGORY_LABELS[category] || item.category;
  const active = checked || selected;
  const tag = mode === "checkbox" ? "label" : "div";
  const activeClass = active ? " is-selected" : "";
  const path = item.relative_path || item.name || "";
  const checkHtml =
    mode === "checkbox"
      ? `<input type="checkbox" class="material-picker-check" value="${escapeAttr(dataPath)}"${checked ? " checked" : ""} aria-label="选择 ${escapeAttr(item.name)}">`
      : `<span class="material-picker-mark" aria-hidden="true">${active ? "✓" : ""}</span>`;
  const dataAttr = mode === "selectable" ? ` data-path="${escapeAttr(dataPath)}"` : "";
  return `
    <${tag} class="material-card material-picker-card${activeClass}"${dataAttr} title="${escapeAttr(item.name)}">
      ${checkHtml}
      <div class="material-thumb">${renderMaterialPickerThumb(item)}</div>
      <div class="material-info">
        <div class="material-name-text">${escapeHtml(item.name)}</div>
        <div class="material-meta">
          <span class="material-tag tag-${category}">${escapeHtml(label)}</span>
          <span>${escapeHtml(path)}</span>
        </div>
      </div>
    </${tag}>`;
}

const PRODUCT_CATEGORY_OPTIONS = [
  { key: "bag", label: "箱包" },
  { key: "shoes", label: "鞋子" },
  { key: "pants", label: "裤子" },
  { key: "top", label: "上衣" },
  { key: "coat", label: "外套" },
  { key: "custom", label: "自定义（自行输入）" },
];

const PRODUCT_CATEGORY_LABEL_BY_KEY = Object.fromEntries(
  PRODUCT_CATEGORY_OPTIONS.map((item) => [item.key, item.label])
);

const PRODUCT_CATEGORY_KEY_BY_LABEL = Object.fromEntries(
  PRODUCT_CATEGORY_OPTIONS.filter((item) => item.key !== "custom").map((item) => [item.label, item.key])
);

function inferProductCategoryKey(label) {
  const text = String(label || "").trim();
  if (!text) return "";
  if (PRODUCT_CATEGORY_KEY_BY_LABEL[text]) return PRODUCT_CATEGORY_KEY_BY_LABEL[text];
  if (PRODUCT_CATEGORY_LABEL_BY_KEY[text]) return text;
  if (text.includes("包") || /bag|tote/i.test(text)) return "bag";
  if (text.includes("鞋") || /shoe/i.test(text)) return "shoes";
  if (text.includes("裤") || /jeans/i.test(text)) return "pants";
  if (text.includes("上衣") || text.includes("T恤") || text.includes("衬衫")) return "top";
  if (text.includes("外套") || text.includes("夹克") || text.includes("大衣")) return "coat";
  if (text.includes("服饰") || text.includes("服装")) return "top";
  return "custom";
}

function syncProductCategoryCustomVisibility(selectId, wrapId, customInputId) {
  const select = document.getElementById(selectId);
  const wrap = document.getElementById(wrapId);
  if (!select || !wrap) return;
  const isCustom = select.value === "custom";
  wrap.hidden = !isCustom;
  const customInput = customInputId ? document.getElementById(customInputId) : wrap.querySelector("input");
  if (customInput) {
    customInput.required = isCustom;
    if (isCustom) {
      window.requestAnimationFrame(() => customInput.focus());
    }
  }
  const hint = document.getElementById("prodCatHint");
  if (hint && selectId === "prodCatKey") {
    hint.hidden = isCustom;
  }
}

function readProductCategoryFields(keySelectId, customInputId) {
  const key = document.getElementById(keySelectId)?.value?.trim() || "";
  const custom = document.getElementById(customInputId)?.value?.trim() || "";
  if (!key) {
    return { product_category_key: null, product_category: null };
  }
  if (key === "custom") {
    return { product_category_key: "custom", product_category: custom || null };
  }
  return {
    product_category_key: key,
    product_category: PRODUCT_CATEGORY_LABEL_BY_KEY[key] || key,
  };
}

function bindProductCategoryControls() {
  const pairs = [
    ["prodCatKey", "prodCatCustomWrap"],
    ["addProductCatKey", "addProductCatCustomWrap"],
  ];
  pairs.forEach(([selectId, wrapId]) => {
    const select = document.getElementById(selectId);
    if (!select || select.dataset.boundCategory) return;
    select.dataset.boundCategory = "1";
    const customInputId =
      selectId === "prodCatKey" ? "prodCatCustom" : selectId === "addProductCatKey" ? "addProductCatCustom" : null;
    select.addEventListener("change", () =>
      syncProductCategoryCustomVisibility(selectId, wrapId, customInputId)
    );
    syncProductCategoryCustomVisibility(selectId, wrapId, customInputId);
  });
  if (!document.body.dataset.productDetailCatBound) {
    document.body.dataset.productDetailCatBound = "1";
    document.body.addEventListener("change", (event) => {
      if (event.target.id !== "productDetailCatKey") return;
      syncProductCategoryCustomVisibility(
        "productDetailCatKey",
        "productDetailCatCustomWrap",
        "productDetailCatCustom"
      );
    });
  }
}

// 会发模型请求的四个接口用这个值：/api/analyze 与 /api/regenerate-directions 发本机识图，
// /api/publish 与 /api/regenerate-content 发文字推理（云端或本机都可能）。后端超时上限是 600 秒，
// 再留 60 秒余量。其余接口仍是 120 秒。
const MODEL_HEAVY_TIMEOUT_MS = 660000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function assetUrl(url) {
  if (!url) return "";
  try {
    if (url.includes("path=")) {
      const marker = url.indexOf("path=");
      const prefix = url.slice(0, marker + 5);
      const raw = url.slice(marker + 5);
      return `${prefix}${encodeURIComponent(decodeURIComponent(raw))}`;
    }
    if (url.includes("file=")) {
      const marker = url.indexOf("file=");
      const prefix = url.slice(0, marker + 5);
      const raw = url.slice(marker + 5);
      return `${prefix}${encodeURIComponent(decodeURIComponent(raw))}`;
    }
    if (url.includes("rel=")) {
      const marker = url.indexOf("rel=");
      const prefix = url.slice(0, marker + 4);
      const raw = url.slice(marker + 4);
      return `${prefix}${encodeURIComponent(decodeURIComponent(raw))}`;
    }
  } catch (_) {}
  return url;
}

function mediaImageUrl(image) {
  if (!image) return "";
  if (image.relative_path) {
    return `/api/package-media?file=${encodeURIComponent(image.relative_path)}`;
  }
  return assetUrl(image.url || "");
}

function historyImageUrl(item, image) {
  if (!image) return "";
  if (image.url && String(image.url).includes("/api/history-media")) {
    return image.url;
  }
  const taskId = item?.id || "";
  const rel = image.relative_path || (image.name ? `generated_images/${image.name}` : "");
  if (taskId && rel) {
    return `/api/history-media?task_id=${encodeURIComponent(taskId)}&file=${encodeURIComponent(rel)}`;
  }
  return mediaImageUrl(image) || assetUrl(image.url || "");
}

async function waitForImageJob(onProgress) {
  const deadline = Date.now() + 660000;
  while (Date.now() < deadline) {
    const job = await api("/api/media-job", {}, 15000);
    if (typeof onProgress === "function") onProgress(job);
    if (job.status === "done") {
      if (job.image_source) {
        job.generated_media = { ...(job.generated_media || {}), image_source: job.image_source };
      }
      return job;
    }
    if (job.status === "error") throw new Error(job.message || "图片生成失败");
    if (job.status === "idle") throw new Error("图片生成任务已中断，请重试");
    await sleep(3000);
  }
  throw new Error("图片生成超时（超过 7 分钟），请稍后重试");
}

const CLOUD_CONSENT_MESSAGE =
  "仅授权本次请求：可能向 AI 设置中的服务发送商品图片、本地文本/文档摘要（可能含文本片段）、商品信息与提示词。服务商可能收费，请确认隐私和费用。\n确定：本次允许云端调用。取消：分析/文案使用本地规则，媒体停止云端生成。";

function requestCloudConsent() {
  return window.confirm(CLOUD_CONSENT_MESSAGE);
}

async function api(path, options = {}, timeoutMs = 120000, apiOptions = {}) {
  const cloudRoutes = new Set([
    "/api/analyze", "/api/regenerate-directions", "/api/publish",
    "/api/regenerate-content", "/api/load-project", "/api/generate-media",
  ]);
  if (cloudRoutes.has(path) && options.method === "POST") {
    const body = JSON.parse(options.body || "{}");
    const needsConsent = path !== "/api/load-project" || body.rerun_analysis === true;
    // No stored authorization: every actual request gets a fresh decision.
    if (needsConsent) {
      if (apiOptions.cloudAuthorized === undefined) {
        body.cloud_authorized = requestCloudConsent();
      } else {
        body.cloud_authorized = Boolean(apiOptions.cloudAuthorized);
      }
    }
    if (path === "/api/generate-media" && (body.generate_image || body.generate_video) && !body.cloud_authorized) {
      throw new Error("已取消本次云端媒体生成，未发送资料");
    }
    options = { ...options, body: JSON.stringify(body) };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 405) {
        throw new Error("后端接口未更新，请关闭网站后重新运行 启动网站.bat");
      }
      const detail = data.detail;
      const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail[0]?.msg : null;
      throw new Error(message || response.statusText || "请求失败");
    }
    return data;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("请求超时，请检查网站是否在运行后重试");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function tagsHtml(tags) {
  return (tags || []).map((tag) => `<span class="tag">${tag}</span>`).join("");
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function showToast(message) {
  const toast = document.getElementById("toastMsg");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2400);
}

let loadingElapsedTimer = null;
let loadingStartedAt = 0;

function formatClock(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function showLoading(text, subtext) {
  const overlay = document.getElementById("loadingOverlay");
  const title = document.getElementById("loadingText");
  const hint = document.getElementById("loadingSubtext");
  if (title) title.textContent = text;
  if (hint) hint.textContent = subtext || "";
  overlay?.classList.add("active");
  const elapsed = document.getElementById("loadingElapsed");
  loadingStartedAt = Date.now();
  if (elapsed) elapsed.textContent = "00:00";
  clearInterval(loadingElapsedTimer);
  loadingElapsedTimer = setInterval(() => {
    const ticker = document.getElementById("loadingElapsed");
    if (ticker) ticker.textContent = formatClock(Date.now() - loadingStartedAt);
  }, 250);
}

function hideLoading() {
  clearInterval(loadingElapsedTimer);
  loadingElapsedTimer = null;
  document.getElementById("loadingOverlay")?.classList.remove("active");
}

function showElapsedSince(startedAt, label) {
  const line = document.getElementById("sidebarTiming");
  if (!line || !startedAt) return;
  line.textContent = `${label} ${((Date.now() - startedAt) / 1000).toFixed(1)} 秒`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function showPage(pageId) {
  document.querySelectorAll(".page").forEach((page) => page.classList.remove("active"));
  const target = document.getElementById(`page-${pageId}`);
  if (target) target.classList.add("active");
  window.scrollTo(0, 0);
  if (pageId === "material") refreshMaterialPage();
  if (pageId === "product") refreshProductPage();
  if (pageId === "history") refreshHistoryPage();
}

function showNavPage(pageId, navLink) {
  showPage(pageId);
  document.querySelectorAll(".nav-links a").forEach((link) => link.classList.remove("active"));
  if (navLink) navLink.classList.add("active");
}

async function startCreativeFlowNav(navLink) {
  await startCreativeFlow();
  document.querySelectorAll(".nav-links a").forEach((link) => link.classList.remove("active"));
  if (navLink) navLink.classList.add("active");
}

function clearCreativeTaskStepUI() {
  const understandingGrid = document.getElementById("understandingGrid");
  if (understandingGrid) understandingGrid.innerHTML = "";
  const directionsGrid = document.getElementById("directionsGrid");
  if (directionsGrid) directionsGrid.innerHTML = "";
  ["copyContent", "visualContent", "videoContent", "generatedImagesContent", "videoPreviewArea", "imagePromptsContent", "step4ReferenceGrid"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.innerHTML = "";
  });
  const exportBox = document.getElementById("exportPackagePath");
  if (exportBox) exportBox.innerHTML = "";
  const step4Subtitle = document.getElementById("step4Subtitle");
  if (step4Subtitle) step4Subtitle.textContent = "基于所选创意方向，为你生成的完整创意内容";
  ["imageGenPrompt", "imageNegPrompt", "videoGenPrompt"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.value = "";
  });
  const toggleImage = document.getElementById("generateImageToggle");
  if (toggleImage) toggleImage.checked = false;
  const toggleVideo = document.getElementById("generateVideoToggleVideo");
  if (toggleVideo) toggleVideo.checked = false;
}

function resetCreativeTaskState() {
  state.materialPath = "";
  state.analysis = null;
  state.publish = null;
  state.selectedDirectionId = null;
  state.materialItems = [];
  state.materialImages = [];
  state.selectedReferenceImages = [];
  state.imagesConfirmed = false;
  state.materialsReady = false;
  state.materialStats = null;
  state.productForm = {};
  state.imagePrompts = [];
  state.generatedMedia = {};
  state.referenceFiles = [];
  state.understandingNotes = "";
  state.pickerDraftPaths = null;
  state.generateImage = false;
  state.generateVideo = false;
  state.creativeTaskCompleted = false;
  state.step5ExportSnapshot = null;
  document.querySelectorAll(".demo-pick").forEach((node) => node.classList.remove("selected"));
  const localInput = document.getElementById("localFileInput");
  if (localInput) localInput.value = "";
  clearProductFormUI();
  clearCreativeTaskStepUI();
  renderUploadPreview();
  renderReferenceFileList();
}

function clearProductFormUI() {
  ["prodName", "prodPrice", "prodDesc", "prodBrand", "prodCatKey", "prodCatCustom"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.value = "";
  });
  syncProductCategoryCustomVisibility("prodCatKey", "prodCatCustomWrap", "prodCatCustom");
}

async function resetCreativeTask() {
  try {
    await api("/api/creative-task/reset", { method: "POST", body: "{}" });
  } catch (error) {
    showToast(error.message || "无法重置任务状态，已清空本地表单");
  }
  resetCreativeTaskState();
}

async function startCreativeFlow(options = {}) {
  const { reset = true } = options;
  if (reset) {
    state.step5ExportSnapshot = null;
    await resetCreativeTask();
  }
  showPage("step1");
  document.querySelectorAll(".nav-links a").forEach((link) => link.classList.remove("active"));
  document.querySelector('.nav-links a[data-action="start-flow-nav"]')?.classList.add("active");
}

function returnHomeFromCompletedTask() {
  state.step5ExportSnapshot = null;
  state.publish = null;
  state.creativeTaskCompleted = false;
  showNavPage("home");
}

function goToStep1() {
  showPage("step1");
}

function collectProductForm() {
  const category = readProductCategoryFields("prodCatKey", "prodCatCustom");
  return {
    product_name: document.getElementById("prodName")?.value?.trim() || null,
    product_category: category.product_category,
    product_category_key: category.product_category_key,
    product_price: document.getElementById("prodPrice")?.value?.trim() || null,
    product_desc: document.getElementById("prodDesc")?.value?.trim() || null,
    product_brand: document.getElementById("prodBrand")?.value?.trim() || null,
  };
}

function applyProductForm(form) {
  if (!form) return;
  const normalized = { ...form };
  if (isPlaceholderProductName(normalized.product_name)) {
    normalized.product_name = null;
  }
  state.productForm = normalized;
  const map = {
    prodName: normalized.product_name,
    prodPrice: normalized.product_price,
    prodDesc: normalized.product_desc,
    prodBrand: normalized.product_brand,
  };
  Object.entries(map).forEach(([id, value]) => {
    const node = document.getElementById(id);
    if (!node) return;
    if (id === "prodName" && isPlaceholderProductName(value)) {
      if (isPlaceholderProductName(node.value)) node.value = "";
      return;
    }
    if (value) node.value = value;
  });
  const catKey = normalized.product_category_key || inferProductCategoryKey(normalized.product_category);
  const keyNode = document.getElementById("prodCatKey");
  if (keyNode) {
    keyNode.value = catKey && catKey !== "all" ? catKey : "";
    syncProductCategoryCustomVisibility("prodCatKey", "prodCatCustomWrap", "prodCatCustom");
  }
  const customNode = document.getElementById("prodCatCustom");
  if (customNode) {
    customNode.value = catKey === "custom" ? normalized.product_category || "" : "";
  }
}

function getActiveProductForm() {
  return collectProductForm();
}

function bindProductFormInputs() {
  bindProductCategoryControls();
  ["prodName", "prodCatKey", "prodCatCustom", "prodPrice", "prodDesc", "prodBrand"].forEach((id) => {
    const node = document.getElementById(id);
    if (!node || node.dataset.formBound === "1") return;
    node.dataset.formBound = "1";
    node.addEventListener("input", () => {
      state.productForm = collectProductForm();
    });
  });
}

async function saveProductForm() {
  const form = collectProductForm();
  state.productForm = { ...form };
  await api("/api/product-form", { method: "POST", body: JSON.stringify(form) });
  return form;
}

function countMaterialFiles() {
  const refs = referenceFilesForDisplay();
  if (refs.length) return refs.length;
  if (state.materialItems?.length) return libraryMaterialItemsForDisplay().length || state.materialItems.length;
  if (state.materialImages?.length) return state.materialImages.length;
  if (state.selectedReferenceImages?.length) return state.selectedReferenceImages.length;
  return 0;
}

function syncPreviewFromCatalog(catalog) {
  applyCatalog(catalog);
  if (catalog.material_path) state.materialPath = catalog.material_path;
  if (catalog.materials_ready || catalog.images_confirmed) {
    state.materialsReady = true;
    state.imagesConfirmed = true;
  } else if (
    !Array.isArray(catalog.reference_images) &&
    (state.materialImages.length || state.referenceFiles.length)
  ) {
    state.selectedReferenceImages = (state.materialImages || []).slice(0, 10);
    state.imagesConfirmed = true;
    state.materialsReady = true;
  }
  renderUploadPreview();
  renderReferenceFileList();
}

async function autoConfirmMaterials() {
  if (!state.materialPath && !state.materialImages.length && !state.referenceFiles.length) {
    return false;
  }
  syncPreviewFromCatalog({
    material_path: state.materialPath,
    items: state.materialItems,
    images: state.materialImages,
    reference_files: state.referenceFiles,
    reference_images: state.selectedReferenceImages,
    materials_ready: state.materialsReady,
    images_confirmed: state.imagesConfirmed,
  });
  if (!state.materialPath) return state.materialsReady;
  try {
    const data = await api("/api/materials/confirm", {
      method: "POST",
      body: JSON.stringify({}),
    });
    syncPreviewFromCatalog(data);
    return Boolean(data.materials_ready);
  } catch (error) {
    if (countMaterialFiles() > 0) {
      state.materialsReady = true;
      state.imagesConfirmed = true;
      if (!state.selectedReferenceImages.length && state.materialImages.length) {
        state.selectedReferenceImages = state.materialImages.slice(0, 10);
      }
      renderUploadPreview();
      return true;
    }
    showToast(error.message);
    return false;
  }
}

async function pickFolder(openPicker = true, target = "task") {
  const data = await api("/api/pick-folder", {
    method: "POST",
    body: JSON.stringify({ initial_dir: state.materialPath || null, target }),
  });
  if (!data.path) {
    showToast("未选择文件夹");
    return null;
  }
  const folderChanged = state.materialPath && state.materialPath !== data.path;
  applyCatalog(data);
  state.materialPath = data.path;
  if (folderChanged) {
    state.selectedReferenceImages = [];
    state.imagesConfirmed = false;
    state.materialsReady = false;
    state.pickerDraftPaths = null;
  }
  if (target === "library") {
    applyLibraryCatalog(data);
    showToast(`已导入 ${data.added_count || 0} 个文件到素材库`);
    return data.path;
  }
  const fileCount = (data.items || data.reference_files || []).length;
  showToast(fileCount ? `已载入 ${fileCount} 个参考资料` : "文件夹已选择，但未发现可分析文件");
  await autoConfirmMaterials();
  return data.path;
}

async function pickLocalFiles(target = "task") {
  const data = await api("/api/pick-files", {
    method: "POST",
    body: JSON.stringify({
      initial_dir: state.materialPath || null,
      force_new_folder: false,
      target,
    }),
  });
  if (!data.added_count) {
    showToast("未选择文件");
    return;
  }
  if (target === "library") {
    applyLibraryCatalog(data);
    showToast(`已保存 ${data.added_count} 个文件到素材库`);
    return;
  }
  applyCatalog(data);
  state.materialPath = data.material_path;
  showToast(`已添加 ${data.added_count} 个文件`);
  await autoConfirmMaterials();
}

async function uploadFilesFromInput(input, target = "task") {
  const files = input?.files;
  if (!files?.length) return;
  const formData = new FormData();
  Array.from(files).forEach((file) => formData.append("files", file));
  const uploadUrl =
    target === "library"
      ? "/api/material-library/upload"
      : `/api/upload-files?target=${encodeURIComponent(target)}`;
  const response = await fetch(uploadUrl, {
    method: "POST",
    body: formData,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : response.status === 405
          ? "后端未更新，请关闭网站后重新运行 启动网站.bat"
          : response.statusText || "上传失败";
    throw new Error(message);
  }
  if (target === "library") {
    applyLibraryCatalog(data);
    showToast(`已保存 ${data.added_count} 个文件到素材库`);
    input.value = "";
    return;
  }
  syncPreviewFromCatalog(data);
  showToast(`已上传 ${data.added_count} 个文件，已加入预览`);
  input.value = "";
}

function triggerLocalFileUpload(target = "task") {
  const inputId = target === "library" ? "libraryFileInput" : "localFileInput";
  const input = document.getElementById(inputId);
  if (!input) return;
  input.onchange = async () => {
    try {
      await uploadFilesFromInput(input, target);
    } catch (error) {
      showToast(error.message);
      input.value = "";
    }
  };
  input.click();
}

function getLibraryPickerDraftSet() {
  if (!state.libraryPickerDraftPaths) {
    state.libraryPickerDraftPaths = new Set();
  }
  return state.libraryPickerDraftPaths;
}

async function openLibraryPickerModal(mode = "all") {
  state.libraryPickerMode = mode === "images" ? "images" : "all";
  state.libraryPickerFolderId = null;
  let data;
  try {
    data = await api("/api/material-library");
  } catch (error) {
    showToast(error.message);
    return;
  }
  state.materialFolders = data.folders || state.materialFolders || [];
  const libraryItems = (data.reference_files || data.items || []).map((item) => ({
    ...item,
    category: normalizeMaterialCategory(item.category),
    source: "library",
  }));
  let taskItems = [];
  if (state.materialPath) {
    try {
      const taskCatalog = await api("/api/material-preview", {
        method: "POST",
        body: JSON.stringify({ material_path: state.materialPath }),
      });
      taskItems = (taskCatalog.reference_files || taskCatalog.items || []).map((item) => ({
        ...item,
        source: "task",
      }));
    } catch (_) {
      /* ignore */
    }
  }
  const merged = new Map();
  [...libraryItems, ...taskItems].forEach((item) => {
    if (item?.relative_path) merged.set(item.relative_path, item);
  });
  state.libraryItems = Array.from(merged.values());
  state.libraryPickerDraftPaths = new Set();
  const subtitle = document.getElementById("libraryPickerSubtitle");
  if (subtitle) {
    subtitle.textContent =
      state.libraryPickerMode === "images"
        ? "可按素材夹筛选图片，多选后点「导入到当前任务」"
        : "可从「全部素材」或「我的素材夹」中多选，选完后点「导入到当前任务」";
  }
  renderLibraryPickerFolders();
  renderLibraryPickerGrid();
  document.getElementById("libraryPickerModal")?.classList.add("active");
}

function libraryPickerVisibleItems() {
  let items = state.libraryItems || [];
  const folderId = state.libraryPickerFolderId;
  if (folderId) {
    const folder = (state.materialFolders || []).find((item) => item.id === folderId);
    const paths = new Set(folder?.paths || []);
    items = items.filter((item) => paths.has(item.relative_path));
  }
  if (state.libraryPickerMode === "images") {
    items = items.filter((item) => item.is_image);
  }
  return items;
}

function renderLibraryPickerFolders() {
  const host = document.getElementById("libraryPickerFolders");
  if (!host) return;
  const folders = state.materialFolders || [];
  if (!folders.length) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  host.hidden = false;
  const activeId = state.libraryPickerFolderId;
  const chips = [
    `<button type="button" class="library-picker-folder-chip${!activeId ? " is-active" : ""}" data-action="pick-library-folder" data-folder-id="">全部素材</button>`,
    ...folders.map((folder) => {
      const count = folder.count ?? (folder.paths || []).length;
      const active = activeId === folder.id ? " is-active" : "";
      return `<button type="button" class="library-picker-folder-chip${active}" data-action="pick-library-folder" data-folder-id="${escapeHtml(folder.id)}">📁 ${escapeHtml(folder.name)} · ${count}</button>`;
    }),
  ];
  host.innerHTML = chips.join("");
}

function setLibraryPickerFolder(folderId) {
  state.libraryPickerFolderId = folderId || null;
  renderLibraryPickerFolders();
  renderLibraryPickerGrid();
}

function renderLibraryPickerGrid() {
  const grid = document.getElementById("libraryPickerGrid");
  const empty = document.getElementById("libraryPickerEmpty");
  const countNode = document.getElementById("libraryPickerCount");
  if (!grid || !empty) return;
  const items = libraryPickerVisibleItems();
  const selected = getLibraryPickerDraftSet();
  if (!items.length) {
    grid.innerHTML = "";
    empty.style.display = "block";
    const emptyText = empty.querySelector("p");
    if (emptyText) {
      emptyText.textContent = state.libraryPickerFolderId
        ? "该素材夹内暂无可用素材，请切换「全部素材」或先在素材库中添加"
        : state.libraryPickerMode === "images"
          ? "素材库暂无图片"
          : "素材库暂无文件";
    }
    if (countNode) countNode.textContent = "已选 0 个";
    return;
  }
  empty.style.display = "none";
  grid.innerHTML = items
    .map((item) =>
      renderMaterialPickerCard(item, {
        mode: "selectable",
        selected: selected.has(item.relative_path),
        dataPath: item.relative_path,
      })
    )
    .join("");
  grid.querySelectorAll(".material-picker-card[data-path]").forEach((node) => {
    node.addEventListener("click", () => {
      const path = node.dataset.path;
      const draft = getLibraryPickerDraftSet();
      if (draft.has(path)) {
        draft.delete(path);
        node.classList.remove("is-selected");
        const mark = node.querySelector(".material-picker-mark");
        if (mark) mark.textContent = "";
      } else {
        draft.add(path);
        node.classList.add("is-selected");
        const mark = node.querySelector(".material-picker-mark");
        if (mark) mark.textContent = "✓";
      }
      if (countNode) countNode.textContent = `已选 ${draft.size} 个`;
    });
  });
  if (countNode) countNode.textContent = `已选 ${selected.size} 个`;
}

function closeLibraryPicker() {
  state.libraryPickerDraftPaths = null;
  state.libraryPickerFolderId = null;
  state.libraryPickerMode = "all";
  document.getElementById("libraryPickerModal")?.classList.remove("active");
}

async function confirmLibraryPicker() {
  const draft = getLibraryPickerDraftSet();
  if (!draft.size) {
    showToast("请至少选择一个素材");
    return;
  }
  const itemsByPath = new Map((state.libraryItems || []).map((item) => [item.relative_path, item]));
  const libraryPaths = [];
  const taskPaths = [];
  draft.forEach((path) => {
    const item = itemsByPath.get(path);
    if (item?.source === "task") taskPaths.push(path);
    else libraryPaths.push(path);
  });
  const pickerMode = state.libraryPickerMode;
  const selectedPaths = [...draft];
  try {
    if (libraryPaths.length) {
      const data = await api("/api/import-library-files", {
        method: "POST",
        body: JSON.stringify({ relative_paths: libraryPaths }),
      });
      applyCatalog(data);
      state.materialPath = data.material_path;
    } else if (taskPaths.length) {
      await refreshMaterialCatalog();
    }
    closeLibraryPicker();
    await autoConfirmMaterials();
    if (pickerMode === "images") {
      const imagePaths = selectedPaths.filter((path) => itemsByPath.get(path)?.is_image);
      if (imagePaths.length) {
        const refData = await api("/api/reference-images/add", {
          method: "POST",
          body: JSON.stringify({ relative_paths: imagePaths }),
        });
        if (refData.reference_images) {
          state.selectedReferenceImages = refData.reference_images;
          state.imagesConfirmed = true;
        }
        renderStep4ReferenceImages();
        renderUploadPreview();
        showToast(`已添加 ${imagePaths.length} 张参考图到当前任务`);
        return;
      }
    }
    showToast(`已选择 ${selectedPaths.length} 个参考资料`);
  } catch (error) {
    showToast(error.message);
  }
}

function getPickerDraftSet() {
  if (!state.pickerDraftPaths) {
    state.pickerDraftPaths = new Set(state.selectedReferenceImages.map((item) => item.relative_path));
  }
  return state.pickerDraftPaths;
}

function imagesFromDraftPaths(pathSet) {
  return state.materialImages.filter((item) => pathSet.has(item.relative_path));
}

function getPreviewImages() {
  if (state.selectedReferenceImages?.length) return state.selectedReferenceImages;
  if (state.materialImages?.length) return state.materialImages.slice(0, 10);
  return [];
}

async function refreshMaterialCatalog() {
  if (!state.materialPath) return;
  const preview = await api("/api/material-preview", {
    method: "POST",
    body: JSON.stringify({ material_path: state.materialPath }),
  });
  applyCatalog(preview);
}

function updateImagePickerCount() {
  const countNode = document.getElementById("imagePickerCount");
  if (!countNode) return;
  const selected = getPickerDraftSet().size;
  countNode.textContent = `已选 ${selected} / 10 张（可继续点选补充）`;
}

function renderImagePickerGrid() {
  const grid = document.getElementById("imagePickerGrid");
  const empty = document.getElementById("imagePickerEmpty");
  const subtitle = document.getElementById("imagePickerSubtitle");
  if (!grid || !empty) return;

  const images = state.materialImages || [];
  const selectedPaths = getPickerDraftSet();

  if (!images.length) {
    grid.innerHTML = "";
    empty.style.display = "block";
    if (subtitle) subtitle.textContent = "当前文件夹里没有图片文件";
    updateImagePickerCount();
    return;
  }

  empty.style.display = "none";
  if (subtitle) {
    subtitle.textContent = `共 ${images.length} 张，逐张点选后可继续补充，选完点「确定」`;
  }
  grid.innerHTML = images
    .map((image) => {
      const selected = selectedPaths.has(image.relative_path) ? " selected" : "";
      return `
        <div class="picker-thumb${selected}" data-path="${image.relative_path}">
          <img src="${image.url}" alt="${image.name}" loading="lazy">
          <span>${image.name}</span>
        </div>`;
    })
    .join("");

  grid.querySelectorAll(".picker-thumb").forEach((node) => {
    node.addEventListener("click", () => {
      const path = node.dataset.path;
      const draft = getPickerDraftSet();
      if (draft.has(path)) {
        draft.delete(path);
        node.classList.remove("selected");
      } else {
        if (draft.size >= 10) {
          showToast("最多选择 10 张，可先取消一张再添加");
          return;
        }
        draft.add(path);
        node.classList.add("selected");
      }
      updateImagePickerCount();
    });
  });
  updateImagePickerCount();
}

function closeImagePicker(resetDraft = true) {
  if (resetDraft) state.pickerDraftPaths = null;
  document.getElementById("imagePickerModal")?.classList.remove("active");
}

async function openImagePickerModal(mode = "step1") {
  state.imagePickerMode = mode;
  if (!state.materialPath) {
    showToast("请先选择素材文件夹");
    return;
  }
  try {
    await refreshMaterialCatalog();
  } catch (error) {
    showToast(error.message);
    return;
  }
  if (!state.materialImages.length) {
    showToast("该文件夹内没有图片");
    return;
  }
  if (mode === "step4") {
    state.pickerDraftPaths = new Set();
  } else {
    state.pickerDraftPaths = new Set(state.selectedReferenceImages.map((item) => item.relative_path));
  }
  renderImagePickerGrid();
  document.getElementById("imagePickerModal")?.classList.add("active");
}

async function confirmImagePicker() {
  const draft = getPickerDraftSet();
  if (!draft.size) {
    showToast("请至少选择一张图片");
    return;
  }
  const selected = imagesFromDraftPaths(draft);
  const relativePaths = selected.map((item) => item.relative_path);
  state.pickerDraftPaths = null;
  closeImagePicker(false);

  if (state.imagePickerMode === "step4") {
    try {
      const data = await api("/api/reference-images/add", {
        method: "POST",
        body: JSON.stringify({ relative_paths: relativePaths }),
      });
      if (data.reference_images) {
        state.selectedReferenceImages = data.reference_images;
        state.imagesConfirmed = true;
      }
      renderStep4ReferenceImages();
      renderUploadPreview();
      showToast(`已添加 ${relativePaths.length} 张参考图`);
    } catch (error) {
      showToast(error.message);
    }
    return;
  }

  state.selectedReferenceImages = selected;
  state.imagesConfirmed = true;
  renderUploadPreview();
  try {
    const data = await api("/api/select-reference-images", {
      method: "POST",
      body: JSON.stringify({ relative_paths: relativePaths }),
    });
    if (data.reference_images?.length) {
      state.selectedReferenceImages = data.reference_images;
      renderUploadPreview();
    }
    showToast(`已确认 ${state.selectedReferenceImages.length} 张参考图，可进入下一步`);
  } catch (error) {
    showToast(
      error.message.includes("重新运行")
        ? `${error.message}；已保存 ${selected.length} 张，可尝试进入下一步`
        : `已确认 ${selected.length} 张参考图，可进入下一步`
    );
  }
}

async function openFolder(path) {
  if (!path) {
    showToast("暂无可用目录");
    return;
  }
  await api("/api/open-folder", { method: "POST", body: JSON.stringify({ path }) });
  showToast("已在资源管理器中打开");
}

function applyLibraryCatalog(catalog) {
  const items = (catalog.items || []).filter(
    (item) => !LIBRARY_PROTECTED_FILENAMES.has(item.name || libraryMaterialItemName(item))
  );
  state.materialItems = items.map((item) => ({
    ...item,
    category: normalizeMaterialCategory(item.category),
  }));
  state.materialFolders = catalog.folders || [];
  state.materialImages = (catalog.images || []).filter(
    (item) => !LIBRARY_PROTECTED_FILENAMES.has(item.name || libraryMaterialItemName(item))
  );
  state.referenceFiles = (catalog.reference_files || []).filter(
    (item) => !LIBRARY_PROTECTED_FILENAMES.has(item.name || libraryMaterialItemName(item))
  );
  state.materialStats = catalog.stats || null;
  state.libraryPath = catalog.library_path || catalog.material_path || state.libraryPath;
  renderMaterialGrid();
  renderMaterialFolderStats();
  renderMaterialCustomFolders();
  updateMaterialGridSectionTitle();
}

function applyCatalog(catalog) {
  state.materialItems = catalog.items || [];
  state.materialImages = catalog.images || [];
  state.referenceFiles = (catalog.reference_files || []).filter((item) => !isHiddenSystemReferenceFile(item));
  if (Array.isArray(catalog.reference_images)) {
    state.selectedReferenceImages = catalog.reference_images;
  } else if (state.materialImages.length && !state.selectedReferenceImages.length) {
    state.selectedReferenceImages = state.materialImages.slice(0, 10);
  }
  if (typeof catalog.materials_ready === "boolean") {
    state.materialsReady = catalog.materials_ready;
  } else if (catalog.images_confirmed || state.referenceFiles.length || state.materialImages.length) {
    state.materialsReady = true;
  }
  if (typeof catalog.images_confirmed === "boolean") {
    state.imagesConfirmed = catalog.images_confirmed;
  } else if (catalog.materials_ready || state.selectedReferenceImages.length) {
    state.imagesConfirmed = true;
  } else if (state.materialImages.length || state.referenceFiles.length) {
    state.imagesConfirmed = true;
  }
  if (catalog.material_path) state.materialPath = catalog.material_path;
  state.materialStats = catalog.stats || null;
  renderUploadPreview();
  renderReferenceFileList();
  renderMaterialGrid();
  renderMaterialFolderStats();
}

function renderReferenceFileList() {
  const host = document.getElementById("referenceFileList");
  if (!host) return;
  const docs = referenceFilesForDisplay().filter((item) => !item.is_image);
  if (!docs.length) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = docs
    .map(
      (item) => `
      <span class="reference-file-chip">
        📄 ${escapeHtml(item.name)}
        <button type="button" class="ui-dismiss-btn ui-dismiss-btn--inline chip-remove" data-action="delete-task-material" data-path="${item.relative_path}" title="删除">×</button>
      </span>`
    )
    .join("");
}

async function setMaterialPath(path) {
  state.materialPath = path;
  const preview = await api("/api/material-preview", {
    method: "POST",
    body: JSON.stringify({ material_path: path }),
  });
  applyCatalog(preview);
  fillFormFromAnalysis();
}

function renderUploadPreview() {
  const host = document.getElementById("uploadPreview");
  if (host) {
    const previewImages = getPreviewImages();
    const thumbs = previewImages
      .map(
        (image) => `
      <div class="preview-item preview-thumb">
        <div class="preview-thumb-wrap">
          <img src="${image.url}" alt="${escapeHtml(image.name)}">
          <button type="button" class="ui-dismiss-btn ui-dismiss-btn--overlay preview-remove" data-action="delete-task-material" data-path="${escapeHtml(image.relative_path)}" title="删除图片" aria-label="删除图片">×</button>
        </div>
      </div>`
      )
      .join("");
    host.innerHTML = `${thumbs}<div class="preview-item preview-add" data-action="open-image-picker" title="继续添加图片">＋</div>`;
  }
  const hint = document.getElementById("materialPathHint");
  const confirmHint = document.getElementById("uploadConfirmHint");
  if (!hint) return;
  if (!state.materialPath) {
    hint.textContent = "请上传或选择参考资料";
    if (confirmHint) confirmHint.textContent = "支持图片、Excel、文档等，上传后可直接进入下一步";
    return;
  }
  const fileCount = referenceFilesForDisplay().length || libraryMaterialItemsForDisplay().length || state.materialItems.length;
  const folderCount = state.materialImages.length;
  const selectedCount = state.selectedReferenceImages.length;
  const docCount = referenceFilesForDisplay().filter((item) => !item.is_image).length;
  if (state.materialsReady && fileCount) {
    if (folderCount) {
      hint.textContent = `已就绪：${fileCount} 个文件（${selectedCount || folderCount} 张参考图）`;
      if (confirmHint) confirmHint.textContent = "资料已确认，填写商品信息后可进入下一步";
    } else {
      hint.textContent = `已就绪：${docCount} 个文档资料`;
      if (confirmHint) confirmHint.textContent = "AI 将结合文档与商品信息进行分析";
    }
    return;
  }
  if (!fileCount) {
    hint.textContent = "尚未添加参考资料";
    if (confirmHint) confirmHint.textContent = "请本地上传或从素材库选择文件";
    return;
  }
  hint.textContent = `已载入 ${fileCount} 个参考资料，正在确认…`;
  if (confirmHint) confirmHint.textContent = "确认完成后可进入下一步";
}

function renderHomeProjects() {
  const container = document.getElementById("homeProjects");
  if (!container) return;
  const projectCards = (state.projects || [])
    .slice(0, 6)
    .map((project) => {
      const statusClass = project.status === "completed" ? "status-completed" : "status-progress";
      const statusLabel =
        project.status === "demo"
          ? "Demo 素材包"
          : project.status === "completed"
            ? "已完成"
            : "已分析";
      const icon = project.icon || "📦";
      const productId = project.id.startsWith("demo:") ? project.id : project.id;
      const clickId = project.id.startsWith("demo:") ? project.id.split(":")[1] : project.id;
      const handler = project.status === "demo" ? `startDemo('${clickId}')` : `loadProject('${productId}')`;
      return `
      <div class="project-card" onclick="${handler}">
        <div class="project-image" style="font-size:56px;display:flex;align-items:center;justify-content:center;">${icon}</div>
        <div class="project-info">
          <span class="project-status ${statusClass}">${statusLabel}</span>
          <h4>${project.title}</h4>
          <div class="project-meta">
            <span>${project.category}</span>
            <span>${project.package_count ? `${project.package_count} 个内容包` : "点击开始创作"}</span>
          </div>
        </div>
      </div>`;
    })
    .join("");
  container.innerHTML = projectCards;
}

async function refreshProjects() {
  const data = await api("/api/projects");
  state.projects = data.projects || [];
  renderHomeProjects();
}

async function startDemo(demoId) {
  const pack = state.demoPacks.find((item) => item.id === demoId);
  if (!pack) return;
  await setMaterialPath(pack.path);
  document.querySelectorAll(".demo-pick").forEach((node) => {
    node.classList.toggle("selected", node.dataset.demo === demoId);
  });
  startCreativeFlow({ reset: false });
  if (state.materialImages.length) {
    await openImagePickerModal();
  }
}

async function pickDemo(demoId) {
  await startDemo(demoId);
}

async function loadProject(productId, options = {}) {
  showLoading("正在载入项目...", "读取素材与分析结果");
  try {
    const data = await api("/api/load-project", {
      method: "POST",
      body: JSON.stringify({ product_id: productId, rerun_analysis: options.rerunAnalysis ?? false }),
    });
    state.materialPath = data.material_path;
    applyCatalog(data.catalog || {});
    if (data.analysis) {
      state.analysis = data.analysis;
      const first = data.analysis.directions?.directions?.[0];
      state.selectedDirectionId = first?.direction_id || null;
      fillFormFromAnalysis();
      renderUnderstanding();
      renderDirections();
    }
    if (data.product?.run_meta?.product_form) {
      applyProductForm(data.product.run_meta.product_form);
    }
    startCreativeFlow({ reset: false });
    showToast("项目已载入");
  } catch (error) {
    showToast(error.message);
  } finally {
    hideLoading();
  }
}

function fillFormFromAnalysis() {
  const understanding = state.analysis?.understanding;
  if (!understanding) return;
  const facts = {};
  (understanding.sections || []).forEach((section) => {
    (section.facts || []).forEach((fact) => {
      facts[fact.label] = fact.summary;
    });
  });
  const nameInput = document.getElementById("prodName");
  if (
    nameInput &&
    !nameInput.value?.trim() &&
    understanding.product_name &&
    !isPlaceholderProductName(understanding.product_name)
  ) {
    nameInput.value = understanding.product_name;
  }
  const catInput = document.getElementById("prodCat");
  if (catInput && !catInput.value) catInput.value = facts["商品类别"] || facts["品类"] || "";
  const priceInput = document.getElementById("prodPrice");
  if (priceInput && !priceInput.value) priceInput.value = facts["建议价格"] || facts["价格"] || "";
  const descInput = document.getElementById("prodDesc");
  if (descInput && !descInput.value) descInput.value = facts["主要场景"] || facts["材质"] || "";
  const brandInput = document.getElementById("prodBrand");
  if (brandInput && !brandInput.value) brandInput.value = facts["品牌名称"] || facts["品牌"] || "";
}

function renderUnderstanding() {
  const analysis = state.analysis?.ai_analysis;
  const source = analysis?.source;
  // 位置三档：本机 / 云端 / 未标注。v1 过渡配置和调用方显式传入的配置证明不了自己跑在哪，
  // 只能显示未标注，不能因为 source 是 llm 就一口咬定是云端。
  const location = analysis?.execution_location === "local" || analysis?.execution_location === "cloud"
    ? analysis.execution_location : "unknown";
  const originLabel = location === "local" ? "本机模型" : location === "cloud" ? "云端模型" : "未标注位置的模型";
  // textContent 写入：相对文件名永远只当文本，不拼进 innerHTML，也不显示绝对路径。
  const imageNote = location === "local" && source === "llm" && analysis?.analyzed_image
    ? ` · 本次实际分析的图片：${analysis.analyzed_image}` : "";
  const notice = document.querySelector("#page-step2 .understanding-result > p");
  if (notice) notice.textContent = source === "history" ? "来源：历史加载（history），未重新调用 AI" :
    source === "llm" ? `来源：本次${originLabel}分析${imageNote}；经营判断仍基于本地规则与资料` :
    `来源：本地规则（非模型） · ${analysis?.fallback_reason || "AI 未成功/未启用，已降级"}`;
  const understanding = state.analysis?.understanding;
  const judgment = state.analysis?.judgment;
  const grid = document.getElementById("understandingGrid");
  if (!understanding || !grid) return;

  const productForm = state.analysis?.product_form || state.productForm || {};
  const productSection = (understanding.sections || []).find((s) => s.section_id === "product_facts");
  const reviewSection = (understanding.sections || []).find((s) => s.section_id === "review_insights");
  const brandSection = (understanding.sections || []).find((s) => s.section_id === "brand_memory");

  const firstFacts = (section) => (section?.facts || []).slice(0, 2).map((f) => f.summary).join("；");
  const factTags = (section) => (section?.facts || []).slice(0, 3).map((f) => f.label);
  const factSummary = (section, label) =>
    (section?.facts || []).find((fact) => fact.label === label)?.summary || "";

  const displayName =
    productForm.product_name || understanding.product_name || factSummary(productSection, "商品名称") || "未命名商品";
  const displayCategory =
    productForm.product_category || factSummary(productSection, "商品类别") || factSummary(productSection, "品类");
  const displayPrice = productForm.product_price || factSummary(productSection, "建议价格");
  const displayBrand = productForm.product_brand || factSummary(productSection, "品牌名称");
  const displayDesc = productForm.product_desc || factSummary(productSection, "主要场景");
  const displayPositioning = factSummary(productSection, "品类定位");

  const positioningTags = [displayCategory, displayPrice ? `¥${displayPrice}` : null, displayBrand]
    .filter(Boolean)
    .concat(factTags(productSection).filter((tag) => !["商品名称", "商品类别", "建议价格", "品牌名称"].includes(tag)))
    .slice(0, 4);

  const positioningMeta = [displayCategory, displayPrice, displayBrand, displayDesc].filter(Boolean).join(" · ");

  const notesBlock = state.understandingNotes
    ? `<div class="understanding-item" style="margin-top:16px;"><h4>人工修正备注</h4><p>${escapeHtml(state.understandingNotes)}</p></div>`
    : "";

  grid.innerHTML = `
    <div class="understanding-item">
      <h4>品类定位</h4>
      <p>${escapeHtml(displayName)}</p>
      ${displayPositioning ? `<p style="margin-top:8px;font-size:15px;">${escapeHtml(displayPositioning)}</p>` : ""}
      ${positioningMeta ? `<p style="margin-top:8px;font-size:14px;color:var(--text-secondary);">${escapeHtml(positioningMeta)}</p>` : ""}
      <div class="tag-list">${tagsHtml(positioningTags)}</div>
    </div>
    <div class="understanding-item">
      <h4>核心机会</h4>
      <p>${judgment?.opportunity || firstFacts(productSection) || "—"}</p>
      <div class="tag-list">${tagsHtml(["本地证据", "经营判断"])}</div>
    </div>
    <div class="understanding-item">
      <h4>目标人群</h4>
      <p>${judgment?.target_audience || "—"}</p>
      <div class="tag-list">${tagsHtml(factTags(reviewSection))}</div>
    </div>
    <div class="understanding-item">
      <h4>品牌与风险</h4>
      <p>${judgment?.history_risk || firstFacts(brandSection) || "—"}</p>
      <div class="tag-list">${tagsHtml(["品牌记忆", "历史风险"])}</div>
    </div>
    ${notesBlock}`;

  const reviewInsight = document.getElementById("reviewInsight");
  if (reviewInsight) {
    reviewInsight.textContent = judgment?.user_evidence || firstFacts(reviewSection) || "暂无评价洞察";
  }
  const reviewTags = document.getElementById("reviewTags");
  if (reviewTags) reviewTags.innerHTML = tagsHtml(factTags(reviewSection));
}

function renderDirections() {
  const notice = document.querySelector("#page-step3 .flow-header p");
  if (notice) notice.textContent = state.analysis?.direction_source === "history" ?
    "来源：历史加载（history），并非本次新生成" : "来源：本地规则蓝图，非云端 AI 生成方向；请核对与当前商品的适配性";
  const directions = state.analysis?.directions?.directions || [];
  const grid = document.getElementById("directionsGrid");
  if (!grid) return;
  grid.innerHTML = directions
    .map((direction, index) => {
      const selected = state.selectedDirectionId === direction.direction_id ? " selected" : "";
      const meta = [...(direction.scenes || []).slice(0, 2), direction.main_selling_point]
        .filter(Boolean)
        .slice(0, 3)
        .map((item) => `<span class="meta-tag">${item}</span>`)
        .join("");
      return `
        <div class="direction-card${selected}" data-direction-id="${direction.direction_id}" onclick="selectDirection(this)">
          <div class="check">✓</div>
          <div class="direction-number">DIRECTION 0${index + 1}</div>
          <h4>${direction.name}</h4>
          <p class="direction-concept">${direction.main_selling_point || ""}</p>
          <div class="direction-meta">${meta}</div>
        </div>`;
    })
    .join("");
}

// 来源取值只有这四种，认不出一律按本地模板说：宁可少说一次"本机"，也不把没生成的东西说成生成过。
// 查表必须走 hasOwnProperty：历史包里的 generation_sources 是磁盘上来的值，不能让它捞到 constructor 之类的原型成员。
function sourceLabel(table, value) {
  return Object.prototype.hasOwnProperty.call(table, value) ? table[value] : table.template;
}

const SOURCE_STATE_LABELS = {
  cloud: "本次云端生成",
  local: "本次本机模型生成",
  local_truncated: "本机模型没写完，退回本地模板",
  mixed: "模型生成与本地模板混合",
  template: "本地模板（未启用、缺密钥或模型调用没成功时降级）",
};
const SOURCE_CHANNEL_LABELS = {
  cloud: "云端", local: "本机", local_truncated: "本机没写完", template: "本地模板",
};
const SOURCE_CHANNEL_LONG_LABELS = {
  cloud: "云端生成", local: "本机生成", local_truncated: "本机没写完", template: "本地模板",
};

function renderContent() {
  const items = state.publish?.content?.items || [];
  const selected = (state.analysis?.directions?.directions || []).find(
    (d) => d.direction_id === state.selectedDirectionId
  );
  const subtitle = document.getElementById("step4Subtitle");
  if (subtitle && selected) {
    const source = state.publish?.content?.source;
    subtitle.textContent = `基于「${selected.name}」方向 · 来源：${sourceLabel(SOURCE_STATE_LABELS, source)}`;
  }

  const byChannel = {};
  items.forEach((item) => {
    byChannel[item.channel] = item;
  });

  const copyHost = document.getElementById("copyContent");
  if (copyHost) {
    const cards = COPY_PLATFORM_ORDER
      .map((channel) => byChannel[channel])
      .filter(Boolean)
      .map(
        (item) => `
        <div class="platform-copy-card">
          <div class="platform-copy-head">
            <span class="platform-copy-badge">${escapeHtml(CHANNEL_LABELS[item.channel] || item.channel)}</span>
            <h5>${escapeHtml(item.title)} · ${sourceLabel(SOURCE_CHANNEL_LABELS, state.publish?.content?.generation_sources?.[item.channel])}</h5>
          </div>
          <div class="copy-text">${escapeHtml(item.body)}</div>
        </div>`
      )
      .join("");
    copyHost.innerHTML = cards || "<p>暂无文案，请先生成创意内容</p>";
  }

  const visual = byChannel.visual;
  document.getElementById("visualContent").innerHTML = visual
    ? `<div class="copy-item visual-analysis-card"><h5>视觉概念 · ${sourceLabel(SOURCE_CHANNEL_LONG_LABELS, state.publish?.content?.generation_sources?.visual)}</h5><div class="copy-text">${escapeHtml(visual.body)}</div></div>`
    : "<p>暂无视觉方案</p>";

  const video = byChannel.video || byChannel.douyin;
  document.getElementById("videoContent").innerHTML = video
    ? `<div class="copy-item"><h5>${escapeHtml(CHANNEL_LABELS[video.channel] || video.channel)} · ${sourceLabel(SOURCE_CHANNEL_LONG_LABELS, state.publish?.content?.generation_sources?.[video.channel])}</h5><div class="copy-text">${escapeHtml(video.body)}</div></div>`
    : "<p>暂无视频脚本</p>";

  renderStep4ReferenceImages();
  renderImagePrompts();
  renderGeneratedMedia();
  fillDefaultMediaPrompts(byChannel);
}

function fillDefaultMediaPrompts(byChannel) {
  const imagePromptNode = document.getElementById("imageGenPrompt");
  const videoPromptNode = document.getElementById("videoGenPrompt");
  const understanding = state.analysis?.understanding;
  const positioning =
    understanding?.sections
      ?.flatMap((s) => s.facts || [])
      .find((f) => f.label === "品类定位")?.summary || "";
  const visual = byChannel.visual;
  if (imagePromptNode && !imagePromptNode.value.trim()) {
    const base = state.imagePrompts?.[0]?.prompt;
    imagePromptNode.value =
      base ||
      `电商商品图，${state.productForm?.product_name || "商品"}，${positioning || visual?.title || "突出卖点与质感"}，9:16竖图，柔光棚拍`;
  }
  if (videoPromptNode && !videoPromptNode.value.trim()) {
    videoPromptNode.value =
      byChannel.video?.title ||
      `20秒竖屏短视频，${state.productForm?.product_name || "商品"}，${positioning || "展示核心卖点"}`;
  }
}

function syncGenerateImageToggle(source) {
  state.generateImage = Boolean(source?.checked);
}

function syncGenerateVideoToggle(source) {
  state.generateVideo = Boolean(source?.checked);
  const toggle = document.getElementById("generateVideoToggleVideo");
  if (toggle && toggle !== source) toggle.checked = state.generateVideo;
}

function renderStep4ReferenceImages() {
  const grid = document.getElementById("step4ReferenceGrid");
  if (!grid) return;
  const images = state.selectedReferenceImages || [];
  if (!images.length) {
    grid.innerHTML = '<p class="step4-ref-empty">暂无参考图，点击「添加图片」从素材库选择</p>';
    return;
  }
  grid.innerHTML = images
    .map(
      (image) => `
      <div class="step4-ref-thumb">
        <img src="${assetUrl(image.url)}" alt="${image.name}">
        <button type="button" class="ui-dismiss-btn ui-dismiss-btn--overlay step4-ref-remove" data-action="remove-reference-image" data-path="${image.relative_path}" title="删除">×</button>
      </div>`
    )
    .join("");
}

function renderImagePrompts() {
  const host = document.getElementById("imagePromptsContent");
  if (!host) return;
  const prompts = state.imagePrompts || [];
  if (!prompts.length) {
    host.innerHTML = "<p style='color:var(--text-muted);'>点击「生成图片提示词」根据当前方向与参考图生成 AI 绘图提示词</p>";
    return;
  }
  host.innerHTML = prompts
    .map(
      (item) => `
      <div class="prompt-card">
        <h5>${item.title} · ${item.scene} · 本地规则提示词（非云端生成）</h5>
        <div class="prompt-text"><strong>正向：</strong>${item.prompt}</div>
        <div class="prompt-text" style="margin-top:8px;"><strong>负向：</strong>${item.negative_prompt}</div>
        <div class="prompt-text" style="margin-top:4px;font-size:12px;">画幅：${item.aspect_ratio}</div>
      </div>`
    )
    .join("");
}

function mediaPreviewHtml(media) {
  if (!media) return "";
  if (media.url && media.name?.endsWith(".mp4")) {
    return `
      <div class="media-preview">
        <p>来源：本地图片幻灯片合成（非云端 AI 视频）</p>
        <video src="${media.url}" controls playsinline></video>
        <a class="media-download-link" href="${media.url}" download="${media.name}">⬇ 保存视频</a>
      </div>`;
  }
  if (media.url) {
    return `<a class="media-download-link" href="${media.url}" download="${media.name || "download"}">⬇ 下载 ${media.name || "文件"}</a>`;
  }
  if (media.message) {
    return `<p style="font-size:13px;color:var(--text-muted);">${media.message}</p>`;
  }
  return "";
}

function renderGeneratedImages() {
  const area = document.getElementById("generatedImagesContent");
  if (!area) return;
  const images = state.generatedMedia?.images || [];
  if (!images.length) {
    area.innerHTML = "";
    return;
  }
  area.innerHTML = `
    <p>${state.generatedMedia?.image_source === "ai_image" ? "来源：云端 AI 图片" : state.generatedMedia?.image_source === "fallback_copy" ? "来源：参考图副本（fallback_copy），非 AI 生成；云端未成功/未启用" : "来源：历史/未知，不能确认是 AI 生成"}</p>
    <div class="generated-images-grid">
      ${images
        .map(
          (image) => `
        <a class="generated-image-thumb" href="${mediaImageUrl(image)}" download="${image.name}" title="下载 ${image.name}">
          <img src="${mediaImageUrl(image)}" alt="${image.name}" loading="lazy">
        </a>`
        )
        .join("")}
    </div>
    <p style="margin-top:8px;font-size:13px;color:var(--text-muted);">已保存 ${images.length} 张图片到内容包，点击可下载</p>`;
}

function renderGeneratedMedia() {
  renderGeneratedImages();
  const videoArea = document.getElementById("videoPreviewArea");
  const media = state.generatedMedia || {};
  const videoHtml = mediaPreviewHtml(media.video);
  const storyboardHtml = media.storyboard
    ? `<p style="font-size:13px;color:var(--text-muted);">${media.storyboard.message || "已保存分镜图片"}（未安装 ffmpeg 时可用分镜图手动剪辑）</p>`
    : "";
  if (videoArea) videoArea.innerHTML = videoHtml || storyboardHtml;
  const toggleImage = document.getElementById("generateImageToggle");
  const toggleVideo = document.getElementById("generateVideoToggleVideo");
  if (toggleImage) toggleImage.checked = state.generateImage;
  if (toggleVideo) toggleVideo.checked = state.generateVideo;
}

async function deleteTaskMaterial(relativePath, options = {}) {
  if (!relativePath) return;
  if (!options.skipConfirm && !window.confirm("确定删除该参考资料？")) return;
  try {
    const data = await api("/api/task-materials/delete", {
      method: "POST",
      body: JSON.stringify({ relative_path: relativePath }),
    });
    syncPreviewFromCatalog(data);
    if (!countMaterialFiles()) {
      state.materialsReady = false;
      state.imagesConfirmed = false;
    }
    renderUploadPreview();
    renderReferenceFileList();
    renderStep4ReferenceImages();
    if (!options.silent) showToast("已删除参考资料");
    return data;
  } catch (error) {
    showToast(error.message);
    return null;
  }
}

async function deleteLibraryMaterial(relativePath) {
  if (!relativePath) return;
  if (!window.confirm("确定从素材库删除该文件？")) return;
  try {
    const data = await api("/api/material-library/delete", {
      method: "POST",
      body: JSON.stringify({ relative_path: relativePath }),
    });
    applyLibraryCatalog(data);
    showToast("已从素材库删除");
  } catch (error) {
    showToast(error.message);
  }
}

let materialRenamePending = null;

const MATERIAL_RENAME_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;

function openMaterialRenameModal(relativePath, currentName) {
  if (!relativePath) return;
  const fallbackName = currentName || relativePath.split("/").pop() || "";
  materialRenamePending = { relativePath, currentName: fallbackName };
  const modal = document.getElementById("materialRenameModal");
  const input = document.getElementById("materialRenameInput");
  const hint = document.getElementById("materialRenameExtensionHint");
  const extMatch = fallbackName.match(/(\.[^./\\]+)$/);
  if (hint) {
    hint.textContent = extMatch
      ? `请保留扩展名 ${extMatch[1]}，仅修改主文件名`
      : "请包含正确的文件扩展名（如 .png、.xlsx）";
  }
  if (input) input.value = fallbackName;
  modal?.classList.add("active");
  requestAnimationFrame(() => {
    if (!input) return;
    input.focus();
    const dot = fallbackName.lastIndexOf(".");
    if (dot > 0) input.setSelectionRange(0, dot);
    else input.select();
  });
}

function closeMaterialRenameModal() {
  document.getElementById("materialRenameModal")?.classList.remove("active");
  materialRenamePending = null;
}

async function confirmMaterialRename() {
  if (!materialRenamePending) return;
  const input = document.getElementById("materialRenameInput");
  const trimmed = (input?.value || "").trim();
  const { relativePath, currentName } = materialRenamePending;
  if (!trimmed || trimmed === currentName) {
    closeMaterialRenameModal();
    return;
  }
  try {
    const data = await api("/api/material-library/rename", {
      method: "POST",
      body: JSON.stringify({ relative_path: relativePath, new_name: trimmed }),
    });
    closeMaterialRenameModal();
    applyLibraryCatalog(data);
    showToast(`已重命名为「${trimmed}」`);
  } catch (error) {
    showToast(error.message);
  }
}

function renameLibraryMaterial(relativePath, currentName) {
  openMaterialRenameModal(relativePath, currentName);
}

async function deleteProduct(productId) {
  if (!productId || productId.startsWith("demo:")) {
    showToast("演示商品不可删除");
    return;
  }
  if (!window.confirm("确定删除该商品？删除后无法恢复。")) return;
  try {
    await api(`/api/products/${encodeURIComponent(productId)}`, { method: "DELETE" });
    if (state.productDetail.productId === productId) closeProductDetail();
    await refreshProductPage();
    await refreshProjects();
    showToast("商品已删除");
  } catch (error) {
    showToast(error.message);
  }
}

async function removeReferenceImage(relativePath) {
  await deleteTaskMaterial(relativePath, { skipConfirm: true, silent: true });
  showToast("已删除参考图");
}

async function generateImagePrompts() {
  if (!state.publish?.ok) {
    showToast("请先生成创意内容");
    return;
  }
  showLoading("正在生成图片提示词...", "结合创意方向与参考图");
  try {
    const data = await api("/api/generate-image-prompts", { method: "POST", body: "{}" });
    state.imagePrompts = data.image_prompts || [];
    const first = state.imagePrompts[0];
    const imagePromptNode = document.getElementById("imageGenPrompt");
    const imageNegNode = document.getElementById("imageNegPrompt");
    if (first && imagePromptNode) imagePromptNode.value = first.prompt || "";
    if (first && imageNegNode) imageNegNode.value = first.negative_prompt || "";
    renderImagePrompts();
    showToast("已根据本地规则与当前分析生成提示词（未调用云端）");
  } catch (error) {
    showToast(error.message);
  } finally {
    hideLoading();
  }
}

async function generateImages() {
  if (!state.publish?.ok) {
    showToast("请先生成创意内容");
    return;
  }
  showLoading(
    state.generateImage ? "正在生成提示词与图片..." : "正在生成图片提示词...",
    state.generateImage ? "百炼绘图通常需 2–5 分钟，请勿关闭页面" : ""
  );
  try {
    const promptData = await api("/api/generate-image-prompts", { method: "POST", body: "{}" });
    state.imagePrompts = promptData.image_prompts || [];
    const imagePrompt = document.getElementById("imageGenPrompt")?.value?.trim();
    const negativePrompt = document.getElementById("imageNegPrompt")?.value?.trim();
    if (!imagePrompt) {
      showToast("请先填写图片提示词，或点击「从 AI 分析生成提示词」");
      return;
    }
    if (state.generateImage) {
      const started = await api("/api/generate-media", {
        method: "POST",
        body: JSON.stringify({
          generate_image: true,
          generate_video: false,
          image_prompt: imagePrompt,
          negative_prompt: negativePrompt || null,
        }),
      }, 30000);
      if (started.status === "running") {
        const finished = await waitForImageJob((job) => {
          const elapsed = job.elapsed_seconds ? `已等待 ${job.elapsed_seconds} 秒` : "百炼绘图通常需 2–5 分钟";
          showLoading("正在生成图片…", job.message || elapsed);
        });
        state.generatedMedia = { ...state.generatedMedia, ...(finished.generated_media || {}) };
        if (finished.image_source) state.generatedMedia.image_source = finished.image_source;
        state.imagePrompts = finished.image_prompts || state.imagePrompts;
        showToast(finished.message || "图片已生成");
      } else {
        state.generatedMedia = { ...state.generatedMedia, ...(started.generated_media || {}) };
        showToast(started.message || "图片已生成");
      }
    } else {
      showToast("图片提示词已就绪，勾选「是否生成图片」后再点击生成");
    }
    renderImagePrompts();
    renderGeneratedMedia();
  } catch (error) {
    showToast(error.message);
  } finally {
    hideLoading();
  }
}

async function generateVideoOnly() {
  if (!state.publish?.ok) {
    showToast("请先生成创意内容");
    return;
  }
  if (!state.generateVideo) {
    showToast("请先勾选「是否生成视频」");
    return;
  }
  const videoPrompt = document.getElementById("videoGenPrompt")?.value?.trim();
  if (!videoPrompt) {
    showToast("请先填写视频创意提示词");
    return;
  }
  showLoading("正在生成视频...", "根据提示词合成竖屏视频");
  try {
    const data = await api("/api/generate-media", {
      method: "POST",
      body: JSON.stringify({
        generate_image: false,
        generate_video: true,
        video_prompt: videoPrompt,
      }),
    });
    state.imagePrompts = data.image_prompts || state.imagePrompts;
    state.generatedMedia = { ...state.generatedMedia, ...(data.generated_media || {}) };
    renderImagePrompts();
    renderGeneratedMedia();
    if (data.fallback) {
      showToast(data.message || "已保存分镜图片（未检测到 ffmpeg）");
    } else if (data.generated_media?.video) {
      showToast("视频已生成并保存");
    } else {
      showToast("视频生成完成");
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    hideLoading();
  }
}

function formatHistoryTime(iso) {
  if (!iso) return "";
  try {
    const date = new Date(iso);
    return date.toLocaleString("zh-CN", { hour12: false });
  } catch (_) {
    return iso;
  }
}

function historySummaryTags(item) {
  const tags = [];
  if (item.used_file_count) tags.push(`${item.used_file_count} 个素材`);
  if (item.generated_text_count) tags.push(`${item.generated_text_count} 段文案`);
  if (item.has_images) tags.push("图片");
  if (item.has_video) tags.push("视频");
  if (item.has_image_prompts) tags.push("图片提示词");
  if (item.package_id) tags.push(item.package_id);
  return tags;
}

function renderHistoryDetailPanel(item) {
  const panel = document.getElementById("historyDetailPanel");
  const titleNode = document.getElementById("historyDetailTitle");
  const metaNode = document.getElementById("historyDetailMeta");
  const bodyNode = document.getElementById("historyDetailBody");
  if (!panel || !bodyNode || !item) return;

  panel.hidden = false;
  if (titleNode) titleNode.textContent = item.product_name || "未命名商品";
  if (metaNode) {
    const parts = [
      formatHistoryTime(item.archived_at),
      item.direction_name ? `方向：${item.direction_name}` : "",
      item.main_selling_point ? `卖点：${item.main_selling_point}` : "",
    ].filter(Boolean);
    metaNode.textContent = `来源：历史记录（history），未重新生成 · ${parts.join(" · ")}`;
  }

  const usedFiles = item.used_files || [];
  const usedFilesHtml = usedFiles.length
    ? `<div class="history-file-grid">
        ${usedFiles
          .map(
            (file) => `
          <div class="history-file-chip${file.is_reference_image ? " is-reference" : ""}">
            <a href="${escapeHtml(file.url || "#")}" target="_blank" rel="noopener">${escapeHtml(file.name)}</a>
            <span>${escapeHtml(file.category_label || file.category || "文件")}${file.is_reference_image ? " · 参考图" : ""}</span>
          </div>`
          )
          .join("")}
      </div>`
    : `<p class="history-detail-empty">未记录素材文件</p>`;

  const textItems = item.generated_text || [];
  const textHtml = textItems.length
    ? `<div class="history-text-list">
        ${textItems
          .map(
            (block) => `
          <article class="history-text-card">
            <div class="history-text-card-head">
              <strong>${escapeHtml(block.label || block.channel)}</strong>
              <span>${escapeHtml(block.title || "")}</span>
            </div>
            <pre class="history-text-body">${escapeHtml(block.body || block.preview || "")}</pre>
          </article>`
          )
          .join("")}
      </div>`
    : `<p class="history-detail-empty">暂无生成文案</p>`;

  const images = item.generated_images || item.generated_media?.images || [];
  const video = item.generated_video || item.generated_media?.video;
  const storyboard = item.generated_storyboard || item.generated_media?.storyboard;
  const imageHtml = images.length
    ? `<div class="history-media-grid">
        ${images
          .map(
            (image) => {
              const imageUrl = historyImageUrl(item, image);
              return `
          <a class="history-media-thumb" href="${escapeHtml(imageUrl || "#")}" target="_blank" rel="noopener" download="${escapeHtml(image.name || "")}">
            <img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(image.name || "生成图片")}" loading="lazy">
            <span>${escapeHtml(image.name || "图片")}</span>
          </a>`;
            }
          )
          .join("")}
      </div>`
    : `<p class="history-detail-empty">暂无生成图片</p>`;

  const videoHtml = video
    ? `<div class="history-video-block">
        <a class="btn btn-secondary btn-small" href="${escapeHtml(video.url || "#")}" target="_blank" rel="noopener">打开视频 ${escapeHtml(video.name || "")}</a>
      </div>`
    : storyboard
      ? `<div class="history-video-block">
          <p class="history-detail-empty">${escapeHtml(storyboard.message || "视频未生成，已保存分镜素材")}</p>
          ${storyboard.path ? `<button type="button" class="btn btn-ghost btn-small" onclick="openFolder('${String(storyboard.path).replace(/\\/g, "\\\\")}')">打开分镜文件夹</button>` : ""}
        </div>`
      : `<p class="history-detail-empty">暂无生成视频</p>`;

  const prompts = item.image_prompts || [];
  const promptHtml = prompts.length
    ? `<div class="history-prompt-list">
        ${prompts
          .map(
            (prompt) => `
          <article class="history-prompt-card">
            <strong>${escapeHtml(prompt.title || prompt.scene || "图片提示词")}</strong>
            <p>${escapeHtml(prompt.prompt || "")}</p>
          </article>`
          )
          .join("")}
      </div>`
    : "";

  bodyNode.innerHTML = `
    <section class="history-detail-section">
      <h4>使用的文件 <span>${usedFiles.length}</span></h4>
      ${usedFilesHtml}
    </section>
    <section class="history-detail-section">
      <h4>生成的文字内容 <span>${textItems.length}</span></h4>
      ${textHtml}
    </section>
    <section class="history-detail-section">
      <h4>生成的图片 <span>${images.length}</span></h4>
      ${imageHtml}
    </section>
    <section class="history-detail-section">
      <h4>生成的视频</h4>
      ${videoHtml}
    </section>
    ${
      prompts.length
        ? `<section class="history-detail-section">
             <h4>图片提示词 <span>${prompts.length}</span></h4>
             ${promptHtml}
           </section>`
        : ""
    }
    <section class="history-detail-actions">
      ${item.product_id ? `<button type="button" class="btn btn-secondary btn-small" data-action="open-product-detail" data-product-id="${escapeHtml(item.product_id)}">在商品库查看</button>` : ""}
      ${item.package_root ? `<button type="button" class="btn btn-ghost btn-small" onclick="openFolder('${String(item.package_root).replace(/\\/g, "\\\\")}')">打开内容包</button>` : ""}
      ${item.export_folder ? `<button type="button" class="btn btn-ghost btn-small" onclick="openFolder('${String(item.export_folder).replace(/\\/g, "\\\\")}')">打开创意导出</button>` : ""}
      ${item.material_path ? `<button type="button" class="btn btn-ghost btn-small" onclick="openFolder('${String(item.material_path).replace(/\\/g, "\\\\")}')">打开素材文件夹</button>` : ""}
    </section>`;

  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeHistoryDetail() {
  state.historyDetail = null;
  const panel = document.getElementById("historyDetailPanel");
  if (panel) panel.hidden = true;
  document.querySelectorAll(".history-card-active").forEach((node) => node.classList.remove("history-card-active"));
}

async function openHistoryDetail(taskId) {
  if (!taskId) return;
  try {
    const item = await api(`/api/history/${encodeURIComponent(taskId)}`);
    state.historyDetail = item;
    document.querySelectorAll(".history-card").forEach((card) => {
      card.classList.toggle("history-card-active", card.dataset.taskId === taskId);
    });
    renderHistoryDetailPanel(item);
  } catch (error) {
    showToast(error.message);
  }
}

function renderHistoryList() {
  const list = document.getElementById("historyList");
  if (!list) return;
  const items = state.historyItems || [];
  if (!items.length) {
    list.innerHTML = '<p class="history-empty">暂无历史记录，完成一次创意任务后会自动保存</p>';
    closeHistoryDetail();
    return;
  }
  list.innerHTML = items
    .map((item) => {
      const tags = historySummaryTags(item);
      const tagHtml = tags.map((t) => `<span class="history-tag">${escapeHtml(t)}</span>`).join("");
      return `
      <article class="history-card" data-task-id="${escapeHtml(item.id)}">
        <button type="button" class="ui-dismiss-btn ui-dismiss-btn--plain history-card-delete" data-action="delete-history" data-task-id="${escapeHtml(item.id)}" title="删除记录" aria-label="删除记录">×</button>
        <div class="history-card-top">
          <h3 class="history-card-title">${escapeHtml(item.product_name || "未命名商品")}</h3>
          <span class="history-card-time">${formatHistoryTime(item.archived_at)}</span>
        </div>
        <div class="history-card-meta">
          ${item.direction_name ? `创意方向：${escapeHtml(item.direction_name)}` : "创意任务记录"}
          ${item.main_selling_point ? ` · ${escapeHtml(item.main_selling_point)}` : ""}
        </div>
        <div class="history-card-tags">${tagHtml}</div>
        <div class="history-card-actions">
          <button type="button" class="btn btn-primary btn-small" data-action="view-history-detail" data-task-id="${escapeHtml(item.id)}">查看详情</button>
          ${item.package_root ? `<button type="button" class="btn btn-ghost btn-small" onclick="openFolder('${String(item.package_root).replace(/\\/g, "\\\\")}')">打开内容包</button>` : ""}
        </div>
      </article>`;
    })
    .join("");
  if (state.historyDetail?.id) {
    const current = items.find((item) => item.id === state.historyDetail.id);
    if (current) renderHistoryDetailPanel(current);
    else closeHistoryDetail();
  }
}

async function refreshHistoryPage() {
  try {
    const data = await api("/api/history");
    state.historyItems = data.items || [];
    renderHistoryList();
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteHistoryItem(taskId) {
  if (!taskId) return;
  if (!window.confirm("确定删除该历史记录？删除后无法恢复。")) return;
  try {
    await api(`/api/history/${encodeURIComponent(taskId)}`, { method: "DELETE" });
    if (state.historyDetail?.id === taskId) {
      closeHistoryDetail();
    }
    state.historyItems = (state.historyItems || []).filter((item) => item.id !== taskId);
    renderHistoryList();
    showToast("已删除历史记录");
  } catch (error) {
    showToast(error.message);
  }
}

function formatArchiveCategories(categories) {
  if (!categories) return "";
  if (Array.isArray(categories)) return categories.join(" · ");
  return Object.entries(categories)
    .map(([key, value]) => `${key} ${value}`)
    .join(" · ");
}

function renderArchiveSummary(archive) {
  if (!archive) return "";
  const exportInfo = archive.export || {};
  const productInfo = archive.product_library || {};
  const materialInfo = archive.material_library || {};
  const importedCount = materialInfo.imported_count || 0;
  const categoryText = formatArchiveCategories(materialInfo.categories);
  const zipName = exportInfo.zip_path ? exportInfo.zip_path.split(/[/\\]/).pop() : "";
  return `
    <div class="archive-summary-grid">
      <div class="archive-summary-card">
        <h4>创意导出文件夹</h4>
        <p class="archive-summary-path">${exportInfo.export_root || "—"}</p>
        <p class="archive-summary-desc">内容包已复制到本地「创意导出」目录，可直接打开或下载 ZIP。</p>
        <div class="archive-summary-actions">
          ${exportInfo.folder_path ? `<button class="btn btn-ghost btn-small" onclick="openFolder('${String(exportInfo.folder_path).replace(/\\/g, "\\\\")}')">打开导出文件夹</button>` : ""}
          ${exportInfo.export_root ? `<button class="btn btn-ghost btn-small" onclick="openFolder('${String(exportInfo.export_root).replace(/\\/g, "\\\\")}')">打开创意导出根目录</button>` : ""}
          ${zipName ? `<button class="btn btn-secondary btn-small" onclick="downloadCreativeExportZip('${zipName}')">下载 ZIP</button>` : `<button class="btn btn-secondary btn-small" onclick="downloadExportZip()">下载 ZIP</button>`}
        </div>
      </div>
      <div class="archive-summary-card">
        <h4>商品库</h4>
        <p class="archive-summary-desc">已自动保存商品总结与创意内容到商品库。</p>
        <p class="archive-summary-meta">商品 ID：${productInfo.product_id || "—"} · ${productInfo.summary_sections || 0} 个板块</p>
        ${productInfo.product_dir ? `<button class="btn btn-ghost btn-small" onclick="openFolder('${String(productInfo.product_dir).replace(/\\/g, "\\\\")}')">打开商品目录</button>` : ""}
      </div>
      <div class="archive-summary-card">
        <h4>素材库归档</h4>
        <p class="archive-summary-desc">本地上传的参考资料已按类型自动分类保存到素材库（从素材库选取的文件不会重复归档）。</p>
        <p class="archive-summary-meta">已归档 ${importedCount} 个文件${categoryText ? ` · ${categoryText}` : ""}</p>
        ${materialInfo.library_root ? `<button class="btn btn-ghost btn-small" onclick="openFolder('${String(materialInfo.library_root).replace(/\\/g, "\\\\")}')">打开素材库</button>` : ""}
      </div>
    </div>`;
}

function renderExportInfo() {
  const exportInfo = state.publish?.export;
  const archive = state.publish?.archive;
  const boxId = "exportPackagePath";
  let box = document.getElementById(boxId);
  if (!box) {
    const host = document.querySelector("#page-step5 .export-grid");
    if (host) {
      box = document.createElement("div");
      box.id = boxId;
      box.className = "export-path-box";
      host.insertAdjacentElement("afterend", box);
    }
  }
  if (!box) return;
  const files = (exportInfo?.files || []).slice(0, 6).map((f) => f.relative_path).join("<br>");
  const archiveHtml = renderArchiveSummary(archive);
  const fallbackPath = exportInfo?.package_root || "";
  box.innerHTML = archiveHtml
    ? `${archiveHtml}
       ${fallbackPath ? `<div class="export-path-fallback"><strong>工作区内容包：</strong><br>${fallbackPath}
         ${files ? `<div style="margin-top:8px;font-size:13px;color:var(--text-secondary);">${files}</div>` : ""}
       </div>` : ""}`
    : fallbackPath
      ? `<strong>内容包已导出：</strong><br>${fallbackPath}
         <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
           <button class="btn btn-ghost btn-small" onclick="openFolder('${fallbackPath.replace(/\\/g, "\\\\")}')">打开文件夹</button>
           <button class="btn btn-secondary btn-small" onclick="downloadExportZip()">下载 ZIP</button>
         </div>
         ${files ? `<div style="margin-top:8px;font-size:13px;color:var(--text-secondary);">${files}</div>` : ""}`
      : "导出目录暂不可用，请返回上一步重新生成。";
}

function selectDirection(card) {
  document.querySelectorAll(".direction-card").forEach((node) => node.classList.remove("selected"));
  card.classList.add("selected");
  state.selectedDirectionId = card.dataset.directionId;
  const selected = (state.analysis?.directions?.directions || []).find(
    (d) => d.direction_id === state.selectedDirectionId
  );
  const subtitle = document.getElementById("step4Subtitle");
  if (subtitle && selected) {
    subtitle.textContent = `基于「${selected.name}」方向，为你生成的完整创意内容`;
  }
}

async function goToStep2() {
  if (!state.materialPath) {
    showToast("请先上传或从素材库选择参考资料");
    return;
  }
  try {
    await refreshMaterialCatalog();
  } catch (_) {
    /* ignore */
  }
  if (!state.materialsReady) {
    const ok = await autoConfirmMaterials();
    if (!ok && countMaterialFiles() === 0) {
      showToast("请先上传图片或文档资料");
      return;
    }
  }
  if (countMaterialFiles() === 0) {
    showToast("未检测到可分析的参考资料，请重新上传");
    return;
  }
  const productForm = collectProductForm();
  if (!productForm.product_name) {
    showToast("请填写商品名称");
    document.getElementById("prodName")?.focus();
    return;
  }
  if (!productForm.product_category_key || !productForm.product_category || !productForm.product_price) {
    if (productForm.product_category_key === "custom" && !document.getElementById("prodCatCustom")?.value?.trim()) {
      showToast("请选择「自定义」后，填写你的类别名称");
      syncProductCategoryCustomVisibility("prodCatKey", "prodCatCustomWrap", "prodCatCustom");
      document.getElementById("prodCatCustom")?.focus();
      return;
    }
    showToast("请选择商品类别并填写价格");
    if (!productForm.product_category_key) document.getElementById("prodCatKey")?.focus();
    return;
  }
  showLoading("AI 正在理解您的商品...", "正在阅读您上传的资料与商品信息");
  try {
    await saveProductForm();
    state.productForm = { ...productForm };
    state.analysis = await api("/api/analyze", {
      method: "POST",
      body: JSON.stringify({
        material_path: state.materialPath,
        product_form: productForm,
      }),
    }, MODEL_HEAVY_TIMEOUT_MS);
    if (!state.analysis.ok) {
      const reason = state.analysis.message || state.analysis.stopped_at || "分析失败";
      throw new Error(reason);
    }
    const first = state.analysis.directions?.directions?.[0];
    state.selectedDirectionId = first?.direction_id || null;
    state.understandingNotes = state.analysis.understanding_notes || "";
    fillFormFromAnalysis();
    renderUnderstanding();
    showPage("step2");
    showToast("商品理解完成");
  } catch (error) {
    showToast(error.message);
  } finally {
    showElapsedSince(loadingStartedAt, "本次分析用时");
    hideLoading();
  }
}

function goToStep3() {
  const directions = state.analysis?.directions?.directions || [];
  if (!state.selectedDirectionId && directions.length) {
    state.selectedDirectionId = directions[0].direction_id;
  }
  renderDirections();
  showPage("step3");
}

async function ensureBackendAnalysis(cloudAuthorized) {
  const saved = await api("/api/state");
  if (saved.analysis?.ok) return;
  if (!state.materialPath) {
    throw new Error("请先上传商品资料");
  }
  const productForm = state.productForm?.product_name ? state.productForm : collectProductForm();
  showLoading(
    "正在恢复分析会话...",
    "检测到后端会话已失效（常见于重启网站后），正在重新理解商品"
  );
  state.analysis = await api("/api/analyze", {
    method: "POST",
    body: JSON.stringify({
      material_path: state.materialPath,
      product_form: productForm,
    }),
  }, MODEL_HEAVY_TIMEOUT_MS, { cloudAuthorized });
  if (!state.analysis?.ok) {
    throw new Error(state.analysis?.message || "商品分析失败，请返回第二步重试");
  }
  state.productForm = { ...productForm };
  const directionIds = new Set((state.analysis.directions?.directions || []).map((d) => d.direction_id));
  if (!directionIds.has(state.selectedDirectionId)) {
    state.selectedDirectionId = state.analysis.directions?.directions?.[0]?.direction_id || null;
    renderDirections();
  }
  renderUnderstanding();
}

async function goToStep4() {
  const directions = state.analysis?.directions?.directions || [];
  if (!state.selectedDirectionId && directions.length) {
    state.selectedDirectionId = directions[0].direction_id;
  }
  if (!state.selectedDirectionId) {
    showToast("请先选择一个创意方向");
    return;
  }
  if (!state.analysis?.ok) {
    showToast("请先完成商品分析，返回第二步重新运行");
    return;
  }
  const cloudAuthorized = requestCloudConsent();
  showLoading(
    "AI 正在创作内容...",
    "本步生成各平台文案（约 1–3 分钟）；AI 图片在下一步「内容生成」里操作"
  );
  try {
    await ensureBackendAnalysis(cloudAuthorized);
    showLoading(
      "AI 正在创作内容...",
      "本步生成各平台文案（约 1–3 分钟）；AI 图片在下一步「内容生成」里操作"
    );
    state.publish = await api("/api/publish", {
      method: "POST",
      body: JSON.stringify({ direction_id: state.selectedDirectionId }),
    }, MODEL_HEAVY_TIMEOUT_MS, { cloudAuthorized });
    if (!state.publish?.ok) {
      throw new Error(state.publish?.message || "内容生成失败，请返回第二步重新分析");
    }
    renderContent();
    showPage("step4");
    const archive = state.publish?.archive;
    if (archive?.export?.folder_path) {
      const imported = archive.material_library?.imported_count || 0;
      showToast(`内容已导出到「创意导出」，${imported} 个素材已归档到素材库`);
    } else {
      showToast("内容生成完成");
    }
  } catch (error) {
    showToast(error.message || "内容生成失败");
  } finally {
    hideLoading();
  }
}

async function goToStep5() {
  try {
    await api("/api/history/archive", { method: "POST", body: "{}" });
    await refreshHistoryPage();
  } catch (_) {
    /* archive is best-effort */
  }
  const exportSnapshot = state.publish;
  await resetCreativeTask();
  state.creativeTaskCompleted = true;
  if (exportSnapshot) {
    state.step5ExportSnapshot = exportSnapshot;
    state.publish = exportSnapshot;
  }
  renderExportInfo();
  showPage("step5");
  showToast("任务已存档到历史记录");
}

async function regenerateDirections() {
  if (!state.materialPath) {
    showToast("请先选择素材文件夹");
    return;
  }
  showLoading("正在重新生成创意方向...", "重新运行分析流水线");
  try {
    state.analysis = await api("/api/regenerate-directions", { method: "POST", body: "{}" }, MODEL_HEAVY_TIMEOUT_MS);
    if (!state.analysis.ok) throw new Error(state.analysis.message || "重新生成失败");
    const first = state.analysis.directions?.directions?.[0];
    state.selectedDirectionId = first?.direction_id || null;
    renderDirections();
    renderUnderstanding();
    showToast("创意方向已更新");
  } catch (error) {
    showToast(error.message);
  } finally {
    showElapsedSince(loadingStartedAt, "本次分析用时");
    hideLoading();
  }
}

async function regenerateContent() {
  if (!state.selectedDirectionId) {
    showToast("请先选择创意方向");
    return;
  }
  showLoading("正在重新生成内容...", "基于当前方向重新创作");
  try {
    state.publish = await api("/api/regenerate-content", {
      method: "POST",
      body: JSON.stringify({ direction_id: state.selectedDirectionId }),
    }, MODEL_HEAVY_TIMEOUT_MS);
    if (!state.publish.ok) throw new Error(state.publish.message || "重新生成失败");
    renderContent();
    showToast("内容已重新生成");
  } catch (error) {
    showToast(error.message);
  } finally {
    hideLoading();
  }
}

async function editUnderstandingNotes() {
  const current = state.understandingNotes || "";
  const notes = window.prompt("输入你对 AI 理解结果的修正备注（将随本次任务保存）：", current);
  if (notes === null) return;
  const data = await api("/api/understanding-notes", {
    method: "POST",
    body: JSON.stringify({ notes }),
  });
  state.understandingNotes = data.understanding_notes || "";
  renderUnderstanding();
  showToast("修正备注已保存");
}

function renderHelpSectionContent(section) {
  if (!section) return "";
  const summary = section.summary
    ? `<p class="help-section-summary">${escapeHtml(section.summary)}</p>`
    : "";
  if (section.steps?.length) {
    return `${summary}
      <ol class="help-step-list">
        ${section.steps
          .map(
            (step, index) => `
          <li class="help-step-item">
            <span class="help-step-index">${index + 1}</span>
            <div>
              <strong>${escapeHtml(step.title)}</strong>
              <p>${escapeHtml(step.desc)}</p>
            </div>
          </li>`
          )
          .join("")}
      </ol>`;
  }
  if (section.faqs?.length) {
    return `${summary}
      <div class="help-faq-list">
        ${section.faqs
          .map(
            (item) => `
          <details class="help-faq-item">
            <summary>${escapeHtml(item.q)}</summary>
            <p>${escapeHtml(item.a)}</p>
          </details>`
          )
          .join("")}
      </div>`;
  }
  if (section.bullets?.length) {
    return `${summary}
      <ul class="help-bullet-list">
        ${section.bullets.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>`;
  }
  return summary || "<p class=\"help-section-summary\">暂无内容</p>";
}

function setHelpCenterTab(sectionId) {
  const help = state.help;
  if (!help) return;
  const sections = help.sections || [];
  const active = sections.find((item) => item.id === sectionId) || sections[0];
  if (!active) return;

  document.querySelectorAll(".help-center-tab").forEach((node) => {
    node.classList.toggle("active", node.dataset.sectionId === active.id);
  });

  const body = document.getElementById("helpCenterBody");
  if (!body) return;
  body.innerHTML = `
    <div class="help-section-card">
      <div class="help-section-card-head">
        <span class="help-section-icon">${escapeHtml(active.icon || "📘")}</span>
        <div>
          <h3>${escapeHtml(active.title || active.label)}</h3>
          <p class="help-section-label">${escapeHtml(active.label || "")}</p>
        </div>
      </div>
      ${renderHelpSectionContent(active)}
    </div>`;
}

const aiSettingsEditor = { settings: null, capability: "text", saving: false, forcedOff: false, enabledDraft: false, initialDraft: "", localApplied: false, maxImagesBeforeLocal: "" };

const LOCAL_VISION_BASICS = "本机图片理解只走 Ollama 原生 /api/chat，单次只分析 1 张商品图：发送前在内存里把最长边缩到 448px 以内（小图不放大），原图文件不改动。地址只能是本机回环（http://127.0.0.1 或 http://localhost），请求不带密钥，也不会走系统代理。快慢取决于你本机装的是哪个模型，以及它有没有已经被读进内存：第一次调用要先载入模型，会明显比后面几次慢。等待时遮罩里那串数字是真秒表，任务结束后固定一行报本次实际用时；屏幕窄到侧栏收起时那一行不显示，秒表仍在。";

const LOCAL_TEXT_BASICS = "本机文字推理只走 Ollama 的 OpenAI 兼容接口 /v1/chat/completions：API 地址必须写成 http://127.0.0.1:11434/v1 这一种，末尾的 /v1 要自己写，少这一段本程序不会替你补。地址只能是本机回环（http://127.0.0.1 或 http://localhost），请求不带密钥，也不会走系统代理。单次回复有输出上限，模型写到上限还没停时那一栏退回本地模板，来源那行会标出来。快慢取决于你本机装的是哪个模型，以及它有没有已经被读进内存：第一次调用要先载入模型，会明显比后面几次慢。";

function aiSettingsDraft() {
  return JSON.stringify(["aiEnabled", "aiLocation", "aiBaseUrl", "aiModel", "aiTimeout", "aiMaxImages", "aiCredentialAction"].map((id) => {
    const element = document.getElementById(id);
    return id === "aiEnabled" ? element.checked : element.value;
  }));
}

function aiSettingsDirty() {
  return aiSettingsEditor.settings && (aiSettingsDraft() !== aiSettingsEditor.initialDraft || document.getElementById("aiKey").value !== "");
}

function capabilityEntry(capability) {
  return aiSettingsEditor.settings?.capabilities?.[capability] || {};
}

function syncAISettingsFields() {
  const enabled = document.getElementById("aiEnabled");
  const action = document.getElementById("aiCredentialAction").value;
  const location = document.getElementById("aiLocation");
  const isVision = aiSettingsEditor.capability === "vision";
  const local = location.value === "local";
  // 图片理解和文字推理都接了本机，这两格不再强制关闭；还没接本机的能力仍然要强制关闭。
  const mediaCapability = ["image", "video"].includes(aiSettingsEditor.capability);
  if (mediaCapability && local) location.value = "cloud";
  const localBlocked = local && !["vision", "text"].includes(aiSettingsEditor.capability);
  // 磁盘上还留着云端密钥时，切本机必须先显式清除；界面绝不自动清钥，后端也会拒收带密钥的本机条目。
  const needsKeyClear = local && capabilityEntry(aiSettingsEditor.capability).has_api_key === true && action !== "clear";
  const forcedOff = action === "clear" || localBlocked || needsKeyClear;
  if (forcedOff && !aiSettingsEditor.forcedOff) aiSettingsEditor.enabledDraft = enabled.checked;
  if (forcedOff) enabled.checked = false;
  else if (aiSettingsEditor.forcedOff) enabled.checked = aiSettingsEditor.enabledDraft;
  aiSettingsEditor.forcedOff = forcedOff;
  enabled.disabled = forcedOff;
  document.getElementById("aiBaseUrl").required = enabled.checked;
  document.getElementById("aiModel").required = enabled.checked;
  const images = document.getElementById("aiMaxImages");
  if (isVision) {
    if (local && !aiSettingsEditor.localApplied) {
      aiSettingsEditor.maxImagesBeforeLocal = images.value;
      aiSettingsEditor.localApplied = true;
      images.value = "1";
    } else if (!local && aiSettingsEditor.localApplied) {
      images.value = aiSettingsEditor.maxImagesBeforeLocal;
      aiSettingsEditor.localApplied = false;
    }
    images.disabled = local;
    images.required = true;
    images.min = local ? "1" : (enabled.checked ? "1" : "0");
  } else {
    images.disabled = true;
    images.required = false;
  }
  const key = document.getElementById("aiKey");
  key.disabled = action !== "replace";
  key.required = action === "replace";
  if (action !== "replace") key.value = "";
  renderLocalNotice();
  renderProbeControls();
}

function renderLocalNotice() {
  const notice = document.getElementById("aiLocalNotice");
  if (!notice) return;
  const capability = aiSettingsEditor.capability;
  const local = ["vision", "text"].includes(capability)
    && document.getElementById("aiLocation").value === "local";
  if (["image", "video"].includes(capability)) {
    notice.hidden = true;
    notice.textContent = "";
    return;
  }
  if (!local) {
    notice.hidden = true;
    notice.textContent = "";
    return;
  }
  const entry = capabilityEntry(capability);
  const action = document.getElementById("aiCredentialAction").value;
  // 三句话的形状对两个能力是一样的，只有名词和动作名按能力切换：图片理解那一支落出来的字与改动前逐字相同。
  const label = capability === "vision" ? "图片理解" : "文字推理";
  const retryAction = capability === "vision" ? "开始分析" : "生成文案";
  const parts = [capability === "vision" ? LOCAL_VISION_BASICS : LOCAL_TEXT_BASICS];
  if (entry.has_api_key === true && action === "clear") {
    parts.push("你已选择「清除密钥并关闭当前能力」：先点保存，这一步会真的删掉那把云端密钥并把能力关掉，不可恢复。保存成功后回来选本机、填地址和模型再启用。");
  } else if (entry.has_api_key === true) {
    parts.push(`当前${label}还保存着云端密钥，不能直接切到本机。要走两步：① 把「密钥操作」选为「清除密钥并关闭当前能力」并保存；② 回来选本机、填地址和模型并启用。清除不可恢复，之后想回云端必须重新填写密钥。`);
  } else {
    parts.push(`本机这一格不保存密钥，也不能带密钥保存。模型名称填你本机 Ollama 里已经存在的那个（在终端运行 ollama list 就能看到有哪些），本程序不会替你下载模型，也不会校验这个名字是否真的存在：名字填错了这里不会报错，要等到点「${retryAction}」时才会悄悄退回本地规则。保存只写配置：不调用模型，也不会显示「连接成功」。想切回云端时，执行位置选云端并重新填写密钥。`);
  }
  notice.textContent = parts.join("\n\n");
  notice.hidden = false;
}

function needsCloudKeyRemoval() {
  return aiSettingsEditor.capability === "vision"
    && document.getElementById("aiLocation").value === "local"
    && capabilityEntry("vision").has_api_key === true
    && document.getElementById("aiCredentialAction").value !== "clear";
}

function renderAISettings(capability) {
  const entry = aiSettingsEditor.settings.capabilities[capability];
  aiSettingsEditor.capability = capability;
  aiSettingsEditor.forcedOff = false;
  aiSettingsEditor.localApplied = false;
  aiSettingsEditor.enabledDraft = entry.enabled;
  document.getElementById("aiEnabled").checked = entry.enabled;
  document.getElementById("aiLocationLocal").disabled = !["vision", "text"].includes(capability);
  document.getElementById("aiLocation").disabled = ["image", "video"].includes(capability);
  document.getElementById("aiLocation").value = entry.execution_location;
  document.getElementById("aiBaseUrl").value = entry.base_url;
  document.getElementById("aiModel").value = entry.model;
  document.getElementById("aiTimeout").value = entry.timeout_seconds;
  document.getElementById("aiMaxImages").value = entry.max_images ?? 0;
  document.getElementById("aiMaxImagesField").hidden = capability !== "vision";
  document.getElementById("aiKey").value = "";
  document.getElementById("aiCredentialAction").value = "keep";
  const pending = aiSettingsEditor.settings.legacy_migration?.needs_confirmation || [];
  document.getElementById("aiMigrationNotice").textContent = pending.length
    ? "旧配置尚未完成确认。首次保存新版配置后，未启用的能力会停止旧调用；旧图片数量不会自动迁入图片理解。请分别确认各项能力。"
    : "保存新版配置后，各能力只使用各自启用的新版设置。图片/视频生成会把上传的参考图与提示词发送到 OpenAI 兼容接口。";
  const descriptions = {
    text: "文字推理与文案：用于内容包文案增强。选本机时走 Ollama 的 OpenAI 兼容接口，API 地址末尾的 /v1 要自己写。",
    vision: "图片理解：用于商品分析。云端可发送商品图和文字资料；本机固定只发 1 张图。",
    image: "图片生成：使用你上传的参考图 + 提示词，调用 GPT 图片接口（/images/edits）生成新图。需 OpenAI 兼容密钥。",
    video: "视频生成：使用第一张参考图作为首帧 + 提示词，调用 GPT 视频接口（/videos，如 sora-2）生成竖屏视频。未配置时会退回本地 ffmpeg 合成。",
  };
  document.getElementById("aiCapabilityDescription").textContent = descriptions[capability] || "";
  document.querySelectorAll('[data-action="select-ai-capability"]').forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.capability === capability));
  });
  document.getElementById("aiSettingsStatus").textContent =
    `${entry.enabled ? "已启用" : "已关闭"} · ${entry.has_api_key ? "已配置密钥（不回显）" : "未配置密钥"} · ${entry.needs_confirmation ? "旧配置待确认 · " : ""}执行位置：${entry.execution_location === "local" ? "本机" : "云端"} · 尚未测试连接`;
  syncAISettingsFields();
  aiSettingsEditor.initialDraft = aiSettingsDraft();
}

async function showAISettings() {
  try {
    aiSettingsEditor.settings = await api("/api/ai-capabilities");
    renderAISettings("text");
    document.getElementById("aiSettingsModal").classList.add("active");
    document.getElementById("aiBaseUrl").focus();
  } catch (_) { showToast("无法加载 AI 设置，请检查配置文件；未修改设置"); }
}

function closeAISettings() {
  if (aiSettingsEditor.saving || !aiSettingsEditor.settings) return;
  if (aiSettingsDirty() && !window.confirm("关闭将丢弃未保存的输入并清空密钥，是否继续？")) return;
  document.getElementById("aiSettingsForm").reset();
  document.getElementById("aiKey").value = "";
  document.getElementById("aiSettingsModal").classList.remove("active");
  aiSettingsEditor.settings = null;
}

function selectAICapability(capability) {
  if (!aiSettingsEditor.settings || aiSettingsEditor.saving || !["text", "vision", "image", "video"].includes(capability) || capability === aiSettingsEditor.capability) return;
  if (aiSettingsDirty() && !window.confirm("切换将丢弃当前未保存的输入并清空密钥，是否继续？")) return;
  renderAISettings(capability);
}

async function saveAISettings() {
  if (aiSettingsEditor.saving || !aiSettingsEditor.settings) return;
  syncAISettingsFields();
  if (!document.getElementById("aiSettingsForm").reportValidity()) return;
  const capability = aiSettingsEditor.capability;
  const keyInput = document.getElementById("aiKey");
  const action = document.getElementById("aiCredentialAction").value;
  const locationValue = document.getElementById("aiLocation").value;
  const stored = aiSettingsEditor.settings.capabilities[capability];
  // 位置与协议是一对：本机图片理解只会是 ollama_chat，云端仍是 openai_chat。
  const localVision = capability === "vision" && locationValue === "local";
  const defaultAdapter = capability === "image" ? "openai_images"
    : capability === "video" ? "openai_video"
    : (document.getElementById("aiEnabled").checked ? "openai_chat" : "");
  const adapter = localVision ? "ollama_chat"
    : (stored.adapter === "ollama_chat" ? "openai_chat" : (stored.adapter || defaultAdapter));
  const body = {
    enabled: document.getElementById("aiEnabled").checked,
    execution_location: ["image", "video"].includes(capability) ? "cloud" : locationValue,
    adapter: adapter,
    base_url: document.getElementById("aiBaseUrl").value,
    model: document.getElementById("aiModel").value,
    timeout_seconds: Number(document.getElementById("aiTimeout").value),
    credential_action: action,
  };
  if (action === "replace") body.api_key = keyInput.value;
  if (capability === "vision") body.max_images = Number(document.getElementById("aiMaxImages").value);
  aiSettingsEditor.saving = true;
  document.getElementById("aiSettingsSave").disabled = true;
  try {
    aiSettingsEditor.settings = await api(`/api/ai-capabilities/${capability}`, {
      method: "POST", body: JSON.stringify(body),
    });
    renderAISettings(capability);
    document.getElementById("aiSettingsStatus").textContent =
      `当前能力已保存 · ${body.enabled ? "已启用" : "已关闭"} · 尚未测试连接；保存没有发送模型请求`;
  } catch (error) {
    const reason = String(error.message || "");
    const safeReason = !/[\r\n]|https?:\/\//.test(reason) && (!keyInput.value || !reason.includes(keyInput.value))
      && /^(更换地址、协议或执行位置时|启用 |启用云端 |启用图片理解时|地址仅允许|超时应为|图片数量应为|密钥格式无效|请填写有效模型名称|本机图片理解|ollama_chat 协议|AI 能力配置|无法保存 AI 能力配置|请求超时)/.test(reason);
    await reportActualCapabilityState(capability,
      `保存失败，未确认保存成功。${safeReason ? reason : "请检查地址、模型、图片数量和密钥操作"}；尚未测试连接。`);
  } finally {
    keyInput.value = "";
    aiSettingsEditor.saving = false;
    document.getElementById("aiSettingsSave").disabled = false;
  }
}

// 保存失败时磁盘上留下的就是上一次的真实状态（例如「能力已关闭且没有密钥」这种中间态），
// 不能拿手头的草稿冒充已保存结果，所以重新读一次公共接口，只取脱敏字段。
async function reportActualCapabilityState(capability, failureText) {
  const status = document.getElementById("aiSettingsStatus");
  status.textContent = failureText;
  try {
    const fresh = await api("/api/ai-capabilities");
    aiSettingsEditor.settings = fresh;
    const entry = fresh.capabilities?.[capability] || {};
    status.textContent = `${failureText} 磁盘上的真实状态：${capability === "vision" ? "图片理解" : "文字能力"}`
      + ` ${entry.enabled ? "已启用" : "已关闭"} · ${entry.has_api_key ? "已配置密钥（不回显）" : "未配置密钥"}`
      + ` · 执行位置 ${entry.execution_location === "local" ? "本机" : "云端"}`;
  } catch (_) {
    status.textContent = `${failureText} 且当前无法读取已保存的配置，磁盘状态未知。`;
  }
}

// 连接自检的三句界面文字。后端才是判定与话术的来源，这里只负责进行中的那一句和两个前后缀。
// 名字不带能力：图片理解与文字推理共用这三句，两格的说法本来就该一致。
const LOCAL_PROBE_PENDING = "正在测试本机连接。第一次调用要把模型读进内存，可能明显偏慢，请不要关闭这个页面。";
const LOCAL_PROBE_FAILED_PREFIX = "本次测试没通过。";
const LOCAL_PROBE_DRAFT_SUFFIX = "（本次测的是已保存的设置，输入框里的改动还没保存）";

// 这个按钮只对「图片理解 · 本机」与「文字推理 · 本机」两格出现，它们各自都有真实调用链可探。
// 未启用的条目也可能带着 execution_location=local 存在，所以光看执行位置不够，必须同时看能力。
function renderProbeControls() {
  const button = document.getElementById("aiConnectionTest");
  const line = document.getElementById("aiProbeResult");
  if (!button || !line) return;
  const show = ["vision", "text"].includes(aiSettingsEditor.capability)
    && document.getElementById("aiLocation").value === "local";
  button.hidden = !show;
  if (!show) {
    line.hidden = true;
    line.textContent = "";
  }
}

// 全前端唯一会真发模型请求的按钮（一次一个，探的是哪一格取决于当前 tab）。它不写配置、
// 不改「尚未测试连接」那一行，结果只落在自己新增的那一行上——那一行是磁盘真实状态的口径，不能拿探测结果冒充。
async function testLocalConnection() {
  if (aiSettingsEditor.saving || !aiSettingsEditor.settings) return;
  const button = document.getElementById("aiConnectionTest");
  const line = document.getElementById("aiProbeResult");
  if (!button || !line) return;
  // 探针只测磁盘上已保存的那份，输入框里的改动不参与——所以改了没保存时必须说清楚。
  const draftChanged = aiSettingsDirty();
  aiSettingsEditor.saving = true;
  document.getElementById("aiSettingsSave").disabled = true;
  button.disabled = true;
  line.hidden = false;
  line.textContent = LOCAL_PROBE_PENDING;
  try {
    const result = await api(`/api/ai-capabilities/${aiSettingsEditor.capability}/test`,
      { method: "POST", body: "{}" });
    const message = typeof result.message === "string" && result.message.trim()
      ? result.message : "本次测试未能完成，后端没有给出可判定的原因；未确认任何连接结果。";
    line.textContent = (result.ok ? "" : LOCAL_PROBE_FAILED_PREFIX) + message
      + (draftChanged ? LOCAL_PROBE_DRAFT_SUFFIX : "");
  } catch (error) {
    const reason = String(error.message || "");
    const safeReason = !/[\r\n]|https?:\/\//.test(reason)
      && /^(请求超时|后端接口未更新|测试连接不接受任何参数|拒绝非同源请求|AI 能力配置|无法读取 AI 能力配置|本机处理失败)/.test(reason);
    line.textContent = safeReason ? `本次测试未能完成：${reason}`
      : "本次测试未能完成，后端没有给出可判定的原因；未确认任何连接结果。";
  } finally {
    aiSettingsEditor.saving = false;
    document.getElementById("aiSettingsSave").disabled = false;
    button.disabled = false;
  }
}

function showHelp() {
  const help = state.help;
  if (!help) {
    showToast("帮助信息加载中，请稍后再试");
    return;
  }
  const modal = document.getElementById("helpCenterModal");
  const tabs = document.getElementById("helpCenterTabs");
  const title = document.getElementById("helpCenterTitle");
  const subtitle = document.getElementById("helpCenterSubtitle");
  const footer = document.getElementById("helpCenterFooter");
  if (!modal || !tabs) return;

  if (title) title.textContent = help.title || "帮助中心";
  if (subtitle) subtitle.textContent = help.subtitle || "";
  if (footer) footer.textContent = help.footer || "";

  const sections = help.sections || [];
  tabs.innerHTML = sections
    .map(
      (section, index) => `
      <button type="button" class="help-center-tab${index === 0 ? " active" : ""}" data-section-id="${escapeHtml(section.id)}" data-action="help-center-tab">
        <span>${escapeHtml(section.icon || "")}</span>
        <span>${escapeHtml(section.label || section.title)}</span>
      </button>`
    )
    .join("");

  setHelpCenterTab(sections[0]?.id);
  modal.classList.add("active");
  document.body.style.overflow = "hidden";
}

function closeHelpCenter() {
  document.getElementById("helpCenterModal")?.classList.remove("active");
  document.body.style.overflow = "";
}

function watchDemo() {
  const first = state.demoPacks[0];
  if (first) startDemo(first.id);
  else startCreativeFlow();
}

function switchTab(tab, panelId) {
  document.querySelectorAll(".content-tab").forEach((node) => node.classList.remove("active"));
  tab.classList.add("active");
  document.querySelectorAll(".content-panel").forEach((node) => node.classList.remove("active"));
  const panel = document.getElementById(`panel-${panelId}`);
  if (panel) panel.classList.add("active");
}

function simulateUpload() {
  pickFolder(true);
}

function findExportFile(patterns) {
  const files = state.publish?.export?.files || [];
  for (const pattern of patterns) {
    const hit = files.find((file) => file.relative_path.toLowerCase().includes(pattern));
    if (hit) return hit.relative_path;
  }
  return null;
}

function triggerDownload(url, filename) {
  const anchor = document.createElement("a");
  anchor.href = url;
  if (filename) anchor.download = filename;
  anchor.target = "_blank";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

async function downloadCreativeExportZip(zipName) {
  if (!zipName) {
    await downloadExportZip();
    return;
  }
  const link = document.createElement("a");
  link.href = `/api/creative-exports/download?name=${encodeURIComponent(zipName)}`;
  link.download = zipName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  showToast("正在下载创意导出 ZIP");
}

async function downloadExportZip() {
  if (!state.publish?.export?.package_root) {
    showToast("请先生成并导出内容");
    return;
  }
  triggerDownload("/api/export/zip");
  showToast("ZIP 下载已开始");
}

async function simulateExport(type) {
  if (!state.publish?.export?.package_root) {
    showToast("请先在步骤 4 生成内容");
    return;
  }
  if (type === "全包") {
    await downloadExportZip();
    return;
  }
  const patterns =
    type === "文案"
      ? ["content/", ".md", "readme"]
      : type === "视觉"
        ? ["visual", "manifest"]
        : [];
  const relative = findExportFile(patterns);
  if (relative) {
    triggerDownload(`/api/export/download?path=${encodeURIComponent(relative)}`);
    showToast(`正在下载 ${relative}`);
    return;
  }
  await downloadExportZip();
}

function renderMaterialFolderStats() {
  const stats = state.materialStats?.categories || {};
  const mapping = {
    image: "folderStatImage",
    video: "folderStatVideo",
    text: "folderStatText",
  };
  Object.entries(mapping).forEach(([category, id]) => {
    const node = document.getElementById(id);
    if (!node) return;
    const bucket = stats[category] || { count: 0, bytes: 0 };
    node.textContent = `${bucket.count} 个文件 · ${formatBytes(bucket.bytes)}`;
  });
}

function activeMaterialFolder() {
  const id = state.activeMaterialFolderId;
  if (!id) return null;
  return (state.materialFolders || []).find((folder) => folder.id === id) || null;
}

function updateMaterialGridSectionTitle() {
  const title = document.getElementById("materialGridSectionTitle");
  const clearBtn = document.getElementById("materialFolderClearBtn");
  if (!title) return;
  const folder = activeMaterialFolder();
  if (folder) {
    title.textContent = `「${folder.name}」中的素材`;
    if (clearBtn) clearBtn.hidden = false;
    return;
  }
  title.textContent = "全部素材";
  if (clearBtn) clearBtn.hidden = true;
}

function renderMaterialCustomFolders() {
  const host = document.getElementById("materialUserFolders");
  if (!host) return;
  const folders = state.materialFolders || [];
  if (!folders.length) {
    host.innerHTML =
      '<p class="material-user-folders-empty">暂无自定义文件夹。点击「新建文件夹」，把图片、视频、文本资料归类到一起。</p>';
    return;
  }
  host.innerHTML = folders
    .map((folder) => {
      const count = folder.count ?? (folder.paths || []).length;
      const active = state.activeMaterialFolderId === folder.id ? " is-active" : "";
      return `
    <div class="material-user-folder-card${active}" data-folder-id="${escapeAttr(folder.id)}">
      <button type="button" class="material-user-folder-main" data-action="view-material-folder" data-folder-id="${escapeAttr(folder.id)}">
        <span class="material-user-folder-icon" aria-hidden="true">📁</span>
        <span>
          <strong>${escapeHtml(folder.name)}</strong>
          <span class="material-user-folder-meta">${count} 个素材</span>
        </span>
      </button>
      <div class="material-user-folder-actions">
        <button type="button" class="btn btn-ghost btn-small" data-action="view-material-folder" data-folder-id="${escapeAttr(folder.id)}">查看</button>
        <button type="button" class="btn btn-ghost btn-small" data-action="add-to-material-folder" data-folder-id="${escapeAttr(folder.id)}">添加素材</button>
        <button type="button" class="btn btn-ghost btn-small" data-action="delete-material-folder" data-folder-id="${escapeAttr(folder.id)}">删除</button>
      </div>
    </div>`;
    })
    .join("");
}

async function createMaterialFolder() {
  const name = window.prompt("请输入文件夹名称");
  if (!name || !name.trim()) return;
  try {
    const data = await api("/api/material-library/folders", {
      method: "POST",
      body: JSON.stringify({ name: name.trim() }),
    });
    state.materialFolders = data.folders || [];
    renderMaterialCustomFolders();
    showToast("文件夹已创建");
  } catch (error) {
    showToast(error.message);
  }
}

function viewMaterialFolder(folderId) {
  return viewMaterialFolderAsync(folderId);
}

async function viewMaterialFolderAsync(folderId) {
  const id = String(folderId || "").trim();
  if (!id) {
    showToast("无法识别素材夹");
    return;
  }
  try {
    const data = await api("/api/material-library");
    state.materialFolders = data.folders || [];
    const items = (data.items || []).filter(
      (item) => !LIBRARY_PROTECTED_FILENAMES.has(item.name || libraryMaterialItemName(item))
    );
    state.materialItems = items.map((item) => ({
      ...item,
      category: normalizeMaterialCategory(item.category),
    }));
    state.materialStats = data.stats || state.materialStats;
  } catch (error) {
    showToast(error.message);
    return;
  }
  const folder = (state.materialFolders || []).find((item) => item.id === id);
  if (!folder) {
    showToast("素材夹不存在，请刷新页面");
    return;
  }
  state.activeMaterialFolderId = id;
  currentCategory = "all";
  document.querySelectorAll(".material-filters .filter-btn").forEach((node) => {
    node.classList.toggle("active", node.dataset.filterCat === "all");
  });
  renderMaterialGrid();
  renderMaterialFolderStats();
  renderMaterialCustomFolders();
  updateMaterialGridSectionTitle();
  applyFilters(state.materialKeyword);
  document.getElementById("materialGrid")?.scrollIntoView({ behavior: "smooth", block: "start" });
  const count = folder.count ?? (folder.paths || []).length;
  showToast(`正在查看「${folder.name}」· ${count} 个素材`);
}

function clearMaterialFolderView() {
  if (!state.activeMaterialFolderId) return;
  state.activeMaterialFolderId = null;
  renderMaterialCustomFolders();
  updateMaterialGridSectionTitle();
  applyFilters(state.materialKeyword);
}

async function openMaterialFolderAddModal(folderId) {
  const prevActiveFolder = state.activeMaterialFolderId;
  try {
    const data = await api("/api/material-library");
    state.materialFolders = data.folders || [];
    const items = (data.items || []).filter(
      (item) => !LIBRARY_PROTECTED_FILENAMES.has(item.name || libraryMaterialItemName(item))
    );
    state.materialItems = items.map((item) => ({
      ...item,
      category: normalizeMaterialCategory(item.category),
    }));
    state.activeMaterialFolderId = prevActiveFolder;
  } catch (error) {
    showToast(error.message);
    return;
  }
  const folder = (state.materialFolders || []).find((item) => item.id === folderId);
  if (!folder) {
    showToast("素材夹不存在，请刷新页面");
    return;
  }
  state.materialFolderAddTargetId = folderId;
  const modal = document.getElementById("materialFolderAddModal");
  const list = document.getElementById("materialFolderAddList");
  const title = document.getElementById("materialFolderAddTitle");
  if (title) title.textContent = `添加素材到「${folder.name}」`;
  const inFolder = new Set(folder.paths || []);
  if (list) {
    const items = libraryMaterialItemsForDisplay();
    list.innerHTML = items.length
      ? items
          .map((item) => {
            const checked = inFolder.has(item.relative_path);
            return renderMaterialPickerCard(item, {
              mode: "checkbox",
              checked,
              dataPath: item.relative_path,
            });
          })
          .join("")
      : '<p class="material-user-folders-empty">素材库暂无文件，请先在本页「本地上传」</p>';
    const countNode = document.getElementById("materialFolderAddCount");
    const syncFolderAddCount = () => {
      if (!countNode || !list) return;
      const checked = list.querySelectorAll(".material-picker-card input[type='checkbox']:checked").length;
      countNode.textContent = `已选 ${checked} 个`;
    };
    list.querySelectorAll(".material-picker-card input[type='checkbox']").forEach((input) => {
      input.addEventListener("change", () => {
        input.closest(".material-picker-card")?.classList.toggle("is-selected", input.checked);
        syncFolderAddCount();
      });
    });
    syncFolderAddCount();
  }
  if (modal) modal.classList.add("active");
}

function closeMaterialFolderAddModal() {
  const modal = document.getElementById("materialFolderAddModal");
  if (modal) modal.classList.remove("active");
  state.materialFolderAddTargetId = null;
}

async function confirmMaterialFolderAdd() {
  const folderId = state.materialFolderAddTargetId;
  if (!folderId) return;
  const list = document.getElementById("materialFolderAddList");
  const paths = list
    ? [...list.querySelectorAll('input[type="checkbox"]:checked')].map((node) => node.value)
    : [];
  try {
    const data = await api(`/api/material-library/folders/${encodeURIComponent(folderId)}/sync`, {
      method: "POST",
      body: JSON.stringify({ relative_paths: paths }),
    });
    state.materialFolders = data.folders || state.materialFolders;
    const folderName = (state.materialFolders || []).find((item) => item.id === folderId)?.name || "素材夹";
    closeMaterialFolderAddModal();
    renderMaterialCustomFolders();
    viewMaterialFolder(folderId);
    showToast(`已加入 ${paths.length} 个素材到「${folderName}」`);
  } catch (error) {
    showToast(error.message);
  }
}

function openMaterialFolderDeleteModal(folderId) {
  const folder = (state.materialFolders || []).find((item) => item.id === folderId);
  if (!folder) return;
  state.materialFolderDeleteTargetId = folderId;
  const nameNode = document.getElementById("materialFolderDeleteName");
  if (nameNode) {
    nameNode.textContent = `即将删除素材夹「${folder.name}」（${folder.count ?? (folder.paths || []).length} 个素材引用）。文件仍保留在素材库。`;
  }
  document.getElementById("materialFolderDeleteModal")?.classList.add("active");
}

function closeMaterialFolderDeleteModal() {
  document.getElementById("materialFolderDeleteModal")?.classList.remove("active");
  state.materialFolderDeleteTargetId = null;
}

async function confirmMaterialFolderDelete() {
  const folderId = state.materialFolderDeleteTargetId;
  if (!folderId) return;
  try {
    const data = await api(`/api/material-library/folders/${encodeURIComponent(folderId)}`, {
      method: "DELETE",
    });
    state.materialFolders = data.folders || [];
    if (state.activeMaterialFolderId === folderId) {
      state.activeMaterialFolderId = null;
      updateMaterialGridSectionTitle();
    }
    closeMaterialFolderDeleteModal();
    renderMaterialCustomFolders();
    applyFilters(state.materialKeyword);
    showToast("素材夹已删除");
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteMaterialFolder(folderId) {
  openMaterialFolderDeleteModal(folderId);
}

function downloadLibraryMaterial(relativePath, filename) {
  if (!relativePath) return;
  const url = `/api/material-library/download?relative_path=${encodeURIComponent(relativePath)}`;
  triggerDownload(url, filename || undefined);
  showToast("开始下载");
}

function downloadLibraryZip() {
  const folder = activeMaterialFolder();
  let url = "/api/material-library/download-zip";
  if (folder?.id) {
    url += `?folder_id=${encodeURIComponent(folder.id)}`;
  }
  triggerDownload(url);
  showToast(folder ? `正在打包「${folder.name}」` : "正在打包全部素材");
}

function renderMaterialGrid() {
  const grid = document.getElementById("materialGrid");
  const countNode = document.getElementById("materialTotalCount");
  if (!grid) return;
  const cards = libraryMaterialItemsForDisplay().map((item) => {
    const thumb = item.is_image
      ? `<img src="${item.url}" alt="${item.name}" style="width:100%;height:100%;object-fit:cover;">`
      : `<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:28px;">${item.category === "video" ? "🎬" : "📄"}</div>`;
    const deleteBtn = materialCanDeleteFromLibrary(item)
      ? `<button type="button" class="ui-dismiss-btn ui-dismiss-btn--overlay material-card-delete" data-action="delete-library-material" data-path="${escapeHtml(item.relative_path)}" title="删除素材" aria-label="删除素材">×</button>`
      : "";
    const renameBtn = materialCanDeleteFromLibrary(item)
      ? `<button type="button" class="material-card-rename" data-action="rename-library-material" data-path="${escapeAttr(item.relative_path)}" data-name="${escapeAttr(item.name)}" title="重命名" aria-label="重命名">${MATERIAL_RENAME_ICON}</button>`
      : "";
    const downloadBtn = `<button type="button" class="material-card-download" data-action="download-library-material" data-path="${escapeAttr(item.relative_path)}" data-name="${escapeAttr(item.name)}" title="下载到本地" aria-label="下载到本地">⬇</button>`;
      return `
    <div class="material-card" data-category="${normalizeMaterialCategory(item.category)}" data-name="${escapeAttr(item.name)}" data-path="${escapeAttr(item.relative_path)}">
      <div class="material-thumb">${downloadBtn}${deleteBtn}${thumb}</div>
      <div class="material-info">
        <div class="material-name-row">
          <span class="material-name-text" title="${escapeAttr(item.name)}">${escapeHtml(item.name)}</span>${renameBtn}
        </div>
        <div class="material-meta">
          <span class="material-tag tag-${normalizeMaterialCategory(item.category)}">${CATEGORY_LABELS[normalizeMaterialCategory(item.category)] || item.category}</span>
          <span>${item.relative_path}</span>
        </div>
      </div>
    </div>`;
  });
  const addCard = `
    <div class="material-card material-add-card" data-category="all" data-name="" data-action="upload-library-files" title="上传或导入素材">
      <div class="material-thumb material-add-thumb">＋</div>
      <div class="material-info">
        <div class="material-name">添加素材</div>
        <div class="material-meta"><span>本地上传或导入文件夹</span></div>
      </div>
    </div>`;
  grid.innerHTML =
    (cards.join("") || "") + addCard;
  if (countNode) countNode.textContent = String(libraryMaterialItemsForDisplay().length);
  applyFilters(state.materialKeyword);
}

async function refreshMaterialPage() {
  try {
    const data = await api("/api/material-library");
    applyLibraryCatalog(data);
  } catch (error) {
    showToast(error.message);
  }
}

let currentCategory = "all";

function filterByCategory(btn, category) {
  currentCategory = category;
  state.activeMaterialFolderId = null;
  if (btn) {
    document.querySelectorAll(".material-filters .filter-btn").forEach((node) => node.classList.remove("active"));
    btn.classList.add("active");
  } else if (category !== "all") {
    document.querySelectorAll(".material-filters .filter-btn").forEach((node) => {
      node.classList.toggle("active", node.getAttribute("onclick")?.includes(`'${category}'`));
    });
  }
  renderMaterialCustomFolders();
  updateMaterialGridSectionTitle();
  applyFilters(state.materialKeyword);
}

function filterMaterials(keyword) {
  state.materialKeyword = keyword || "";
  applyFilters(state.materialKeyword);
}

function applyFilters(keyword) {
  const cards = document.querySelectorAll(".material-card");
  const searchKeyword = (keyword || "").toLowerCase();
  const folder = activeMaterialFolder();
  const folderPaths = folder
    ? new Set((folder.paths || []).map((path) => normalizeLibraryPath(path)))
    : null;
  let visible = 0;
  cards.forEach((card) => {
    if (card.classList.contains("material-add-card")) {
      card.style.display = "";
      return;
    }
    const cat = normalizeMaterialCategory(card.getAttribute("data-category"));
    const path = normalizeLibraryPath(card.getAttribute("data-path") || "");
    const name = (card.getAttribute("data-name") || "").toLowerCase();
    const filterCat = currentCategory === "all" ? "all" : normalizeMaterialCategory(currentCategory);
    const matchCategory = filterCat === "all" || cat === filterCat;
    const matchFolder = !folderPaths || folderPaths.has(path);
    const matchKeyword = !searchKeyword || name.includes(searchKeyword);
    const show = matchCategory && matchFolder && matchKeyword;
    card.style.display = show ? "" : "none";
    if (show) visible += 1;
  });
  const countNode = document.getElementById("materialTotalCount");
  if (countNode) countNode.textContent = String(visible);
}

function setViewMode(btn, mode) {
  document.querySelectorAll(".material-view-toggle .view-btn").forEach((node) => node.classList.remove("active"));
  if (btn) btn.classList.add("active");
  const grid = document.getElementById("materialGrid");
  if (!grid) return;
  grid.style.gridTemplateColumns = mode === "list" ? "1fr" : "";
}

function productStatusMeta(status) {
  if (status === "completed") {
    return { label: "已生成创意", pill: "pill-success", state: "内容包已导出" };
  }
  if (status === "analyzed") {
    return { label: "已分析", pill: "pill-info", state: "等待生成内容" };
  }
  if (status === "demo") {
    return { label: "Demo", pill: "pill-demo", state: "官方素材包" };
  }
  if (status === "manual") {
    return { label: "手动添加", pill: "pill-info", state: "可编辑资料" };
  }
  return { label: "待创作", pill: "pill-muted", state: "尚未开始分析" };
}

function productCardCategory(product) {
  const raw = String(product.category || "").trim();
  if (raw && PRODUCT_CATEGORY_LABEL_BY_KEY[raw]) return raw;
  if (raw && PRODUCT_CATEGORY_KEY_BY_LABEL[raw]) return PRODUCT_CATEGORY_KEY_BY_LABEL[raw];
  const inferred = inferProductCategoryKey(product.category_label || raw || product.name || "");
  return inferred || "all";
}

function productCoverClass(product) {
  const category = productCardCategory(product);
  if (category === "bag") return "cover-bag";
  if (category === "clothing") return "cover-clothing";
  if (category === "workspace") return "cover-workspace";
  return "";
}

function renderProductGrid() {
  const grid = document.getElementById("productGrid");
  const countNode = document.getElementById("productTotalCount");
  const emptyNode = document.getElementById("productEmpty");
  if (!grid) return;

  if (!state.products.length) {
    grid.innerHTML = "";
    if (emptyNode) emptyNode.style.display = "block";
    if (countNode) countNode.textContent = "0";
    return;
  }
  if (emptyNode) emptyNode.style.display = "none";

  grid.innerHTML = state.products
    .map((product) => {
      const icon = product.icon || "🛍️";
      const price = product.price ? `¥${product.price}` : "价格待定";
      const category = productCardCategory(product);
      const status = productStatusMeta(product.status);
      const tags = [product.category_label, product.brand].filter(Boolean).slice(0, 3);
      const productId = product.id;
      const isWorkspaceProduct = product.source === "workspace" || String(productId).startsWith("prod_");
      const extraTag =
        product.package_count > 0 ? `<span class="product-card-tag">${product.package_count} 个内容包</span>` : "";
      return `
      <article class="product-card-v2 product-card" data-category="${category}" data-name="${product.name}" data-product-id="${productId}">
        <div class="product-card-cover ${productCoverClass(product)}">
          ${
            isWorkspaceProduct
              ? `<button type="button" class="ui-dismiss-btn ui-dismiss-btn--overlay product-card-delete" data-action="delete-product" data-product-id="${productId}" title="删除商品" aria-label="删除商品">×</button>`
              : ""
          }
          <span class="product-card-icon">${icon}</span>
          <span class="product-status-pill ${status.pill}">${status.label}</span>
        </div>
        <div class="product-card-body">
          <div class="product-card-top">
            <span class="product-card-brand">${product.brand || "本地商品"}</span>
            <span class="product-card-price">${price}</span>
          </div>
          <h3 class="product-card-title">${product.name}</h3>
          <div class="product-card-tags">
            ${tags.map((tag) => `<span class="product-card-tag">${tag}</span>`).join("")}
            ${extraTag}
          </div>
          <div class="product-card-footer">
            <span class="product-card-state">${status.state}</span>
            <div class="product-card-actions">
              <button type="button" class="btn btn-ghost btn-small" data-action="view-product-detail" data-product-id="${productId}">详情</button>
              ${
                isWorkspaceProduct
                  ? `<button type="button" class="btn btn-primary btn-small" data-action="edit-product-detail-open" data-product-id="${productId}">修改</button>`
                  : `<button type="button" class="btn btn-primary btn-small" onclick="createFromProduct('${productId}')">创作</button>`
              }
            </div>
          </div>
        </div>
      </article>`;
    })
    .join("");
  if (countNode) countNode.textContent = String(state.products.length);
  applyProductFilters();
}

function renderProductStats(stats) {
  const totalNode = document.getElementById("productStatTotal");
  const completedNode = document.getElementById("productStatCompleted");
  if (totalNode) totalNode.textContent = String(stats?.total ?? state.products.length);
  if (completedNode) completedNode.textContent = String(stats?.completed ?? 0);
}

async function refreshProductPage() {
  try {
    const data = await api("/api/products");
    state.products = data.products || [];
    renderProductStats(data.stats);
    renderProductGrid();
  } catch (error) {
    showToast(error.message);
  }
}

function productDetailCanEdit() {
  const id = state.productDetail.productId;
  return state.productDetail.source === "workspace" || (id && String(id).startsWith("prod_"));
}

function setProductDetailCardHighlight(productId) {
  document.querySelectorAll("#productGrid .product-card").forEach((card) => {
    card.classList.toggle("product-card-active", card.dataset.productId === productId);
  });
}

function renderProductProfileFields(profile, editing) {
  const catKey = profile.product_category_key || inferProductCategoryKey(profile.product_category);
  const catLabel = profile.product_category || PRODUCT_CATEGORY_LABEL_BY_KEY[catKey] || "";
  const fields = [
    { key: "product_name", label: "商品名称", required: true },
    { key: "product_brand", label: "品牌" },
    { key: "product_price", label: "价格" },
  ];
  const grid = fields
    .map((field) => {
      const value = escapeHtml(profile[field.key] || "");
      if (editing) {
        return `
        <label class="product-detail-field">
          <span>${field.label}</span>
          <input type="text" data-profile-key="${field.key}" value="${value}" ${field.required ? "required" : ""}>
        </label>`;
      }
      return `
      <div class="product-detail-field-readonly">
        <span>${field.label}</span>
        <strong>${value || "—"}</strong>
      </div>`;
    })
    .join("");
  const categoryBlock = editing
    ? `<label class="product-detail-field product-detail-field-full">
         <span>类别</span>
         <select data-profile-key="product_category_key" id="productDetailCatKey">
           ${PRODUCT_CATEGORY_OPTIONS.map(
             (opt) =>
               `<option value="${opt.key}" ${catKey === opt.key ? "selected" : ""}>${opt.label}</option>`
           ).join("")}
         </select>
       </label>
       <label class="product-detail-field product-detail-field-full" id="productDetailCatCustomWrap" ${
         catKey === "custom" ? "" : "hidden"
       }>
         <span>自定义类别名称</span>
         <input type="text" id="productDetailCatCustom" data-profile-key="product_category" value="${escapeHtml(catLabel)}" placeholder="例如：配饰、家居、数码…">
       </label>`
    : `<div class="product-detail-field-readonly product-detail-field-full">
         <span>类别</span>
         <strong>${escapeHtml(catLabel || "—")}</strong>
       </div>`;
  const desc = escapeHtml(profile.product_desc || "");
  const descBlock = editing
    ? `<label class="product-detail-field product-detail-field-full">
         <span>商品描述</span>
         <textarea data-profile-key="product_desc" rows="3">${desc}</textarea>
       </label>`
    : `<div class="product-detail-field-readonly product-detail-field-full">
         <span>商品描述</span>
         <p>${desc || "暂无描述"}</p>
       </div>`;
  return `<section class="product-detail-block product-detail-block-full">
    <h4 class="product-detail-block-title">基本信息</h4>
    <div class="product-detail-profile-grid">${grid}${categoryBlock}${descBlock}</div>
  </section>`;
}

function renderProductNotes(profile, editing) {
  const notes = escapeHtml(profile.user_notes || "");
  if (editing) {
    return `<section class="product-detail-block product-detail-block-full">
      <h4 class="product-detail-block-title">补充说明</h4>
      <textarea class="product-detail-section-edit" data-profile-key="user_notes" rows="4" placeholder="手动补充的商品信息、卖点、注意事项…">${notes}</textarea>
    </section>`;
  }
  if (!notes) return "";
  return `<section class="product-detail-block product-detail-block-full">
    <h4 class="product-detail-block-title">补充说明</h4>
    <pre class="product-detail-section-body">${notes}</pre>
  </section>`;
}

function renderProductGeneratedImages(images) {
  if (!images?.length) return "";
  return `<section class="product-detail-block product-detail-block-full">
    <h4 class="product-detail-block-title">AI 生成图片</h4>
    <p class="product-detail-hint-inline">生成图片已自动保存到商品库，可在创意任务或历史记录中继续查看。</p>
    <div class="history-media-grid">
      ${images
        .map(
          (image) => `
        <a class="history-media-thumb" href="${escapeHtml(image.url || "#")}" target="_blank" rel="noopener" download="${escapeHtml(image.name || "")}">
          <img src="${escapeHtml(image.url || "")}" alt="${escapeHtml(image.name || "生成图片")}" loading="lazy">
          <span>${escapeHtml(image.name || "图片")}</span>
        </a>`
        )
        .join("")}
    </div>
  </section>`;
}

function renderProductFiles(files, editing) {
  const list =
    files.length > 0
      ? files
          .map(
            (file) => `
        <div class="product-file-chip">
          <a href="/api/material-file?path=${encodeURIComponent(file.absolute_path)}" target="_blank" rel="noopener">${escapeHtml(file.name)}</a>
          <span class="product-file-tag">${escapeHtml(CATEGORY_LABELS[file.category] || file.category || "文件")}</span>
          ${
            editing
              ? `<button type="button" class="ui-dismiss-btn ui-dismiss-btn--inline chip-remove" data-action="delete-product-file" data-relative-path="${escapeHtml(file.relative_path)}">×</button>`
              : ""
          }
        </div>`
          )
          .join("")
      : `<p class="product-detail-hint-inline">${editing ? "暂无参考资料，可点击「本地上传」添加图片或文档。" : "暂无参考资料。"}</p>`;
  return `<section class="product-detail-block product-detail-block-full">
    <div class="product-detail-block-head">
      <h4 class="product-detail-block-title">参考资料</h4>
      ${editing ? `<button type="button" class="btn btn-ghost btn-small" data-action="upload-product-files">本地上传</button>` : ""}
    </div>
    <div class="product-file-list">${list}</div>
  </section>`;
}

function productDetailSectionBody(sections, sectionId) {
  const block = (sections || []).find((item) => item.id === sectionId);
  return block?.body || "";
}

function renderProductSummarySection(sectionId, title, sections, editing) {
  const body = escapeHtml(productDetailSectionBody(sections, sectionId));
  if (editing) {
    return `<section class="product-detail-block product-detail-block-full product-detail-priority-section">
      <h4 class="product-detail-block-title">${escapeHtml(title)}</h4>
      <textarea class="product-detail-section-edit" data-section-id="${escapeHtml(sectionId)}" rows="5" placeholder="填写${escapeHtml(title)}…">${body}</textarea>
    </section>`;
  }
  return `<section class="product-detail-block product-detail-block-full product-detail-priority-section">
    <h4 class="product-detail-block-title">${escapeHtml(title)}</h4>
    <pre class="product-detail-section-body">${body || `暂无${title}，可点击「修改」后填写。`}</pre>
  </section>`;
}

function renderProductAiSections(sections, editing) {
  const skip = new Set(["visual", "video"]);
  const blocks = (sections || [])
    .filter((section) => !skip.has(section.id))
    .map((section) => {
      const title = escapeHtml(section.title || section.id);
      if (editing) {
        return `
        <article class="product-detail-section">
          <h4 class="product-detail-section-title">${title}</h4>
          <textarea class="product-detail-section-edit" data-section-id="${escapeHtml(section.id)}">${escapeHtml(section.body || "")}</textarea>
        </article>`;
      }
      return `
      <article class="product-detail-section">
        <h4 class="product-detail-section-title">${title}</h4>
        <pre class="product-detail-section-body">${escapeHtml(section.body || "暂无内容，完成创意任务后将自动填充")}</pre>
      </article>`;
    })
    .join("");
  if (!blocks) {
    return `<p class="product-detail-hint-inline">完成创意任务后，AI 理解与多平台文案将显示在这里。</p>`;
  }
  return `<section class="product-detail-block product-detail-block-full">
    <h4 class="product-detail-block-title">AI 创意内容</h4>
    <div class="product-detail-panel-body product-detail-panel-body-nested">${blocks}</div>
  </section>`;
}

function renderProductDetailPanel() {
  const panel = document.getElementById("productDetailPanel");
  const titleNode = document.getElementById("productDetailTitle");
  const metaNode = document.getElementById("productDetailMeta");
  const bodyNode = document.getElementById("productDetailBody");
  const editBtn = document.getElementById("productDetailEditBtn");
  const saveBtn = document.getElementById("productDetailSaveBtn");
  const detail = state.productDetail;
  if (!panel || !bodyNode) return;

  if (!detail.productId) {
    hideProductDetailModal();
    return;
  }

  showProductDetailModal();
  if (titleNode) titleNode.textContent = detail.profile?.product_name || detail.name || detail.productId;
  if (metaNode) {
    const parts = [
      "来源：历史/本地保存资料（history），未重新生成",
      detail.source === "demo" ? "官方 Demo 素材包" : "本地商品库",
      detail.editing ? "编辑模式" : "只读预览",
      detail.files?.length ? `${detail.files.length} 个文件` : "",
    ].filter(Boolean);
    metaNode.textContent = parts.join(" · ");
  }
  if (editBtn) {
    editBtn.textContent = detail.editing ? "取消修改" : "修改";
    editBtn.disabled = detail.loading;
  }
  if (saveBtn) {
    saveBtn.disabled = detail.loading || !detail.editing || !productDetailCanEdit();
  }

  if (detail.loading) {
    bodyNode.innerHTML = '<p class="product-detail-loading">正在加载商品详情…</p>';
    return;
  }

  const profileBlock = renderProductProfileFields(detail.profile || {}, detail.editing);
  const notesBlock = renderProductNotes(detail.profile || {}, detail.editing);
  const visualBlock = renderProductSummarySection("visual", "视觉概念", detail.sections, detail.editing);
  const videoBlock = renderProductSummarySection("video", "视频脚本", detail.sections, detail.editing);
  const filesBlock = renderProductFiles(detail.files || [], detail.editing);
  const aiBlock = renderProductAiSections(detail.sections, detail.editing);

  bodyNode.innerHTML = `
    ${profileBlock}
    ${notesBlock}
    ${visualBlock}
    ${videoBlock}
    ${filesBlock}
    ${aiBlock}`;
}

function collectProductDetailProfile() {
  const bodyNode = document.getElementById("productDetailBody");
  const profile = { ...(state.productDetail.profile || {}) };
  if (!bodyNode) return profile;
  bodyNode.querySelectorAll("[data-profile-key]").forEach((node) => {
    profile[node.dataset.profileKey] = node.value;
  });
  return profile;
}

function collectProductDetailSections() {
  const bodyNode = document.getElementById("productDetailBody");
  if (!bodyNode) return state.productDetail.sections;
  return state.productDetail.sections.map((section) => {
    const textarea = bodyNode.querySelector(`textarea[data-section-id="${section.id}"]`);
    if (!textarea) return section;
    return { ...section, body: textarea.value };
  });
}

function closeProductDetail() {
  state.productDetail = {
    productId: null,
    name: "",
    source: "workspace",
    editing: false,
    loading: false,
    profile: {},
    sections: [],
    files: [],
  };
  setProductDetailCardHighlight(null);
  hideProductDetailModal();
  renderProductDetailPanel();
}

async function openProductDetail(productId, options = {}) {
  if (!productId) return;
  const sameProduct = state.productDetail.productId === productId;
  const startEditing = Boolean(options.editing);
  state.productDetail = {
    productId,
    name: "",
    source: "workspace",
    editing: startEditing || (sameProduct ? state.productDetail.editing : false),
    loading: true,
    profile: {},
    sections: [],
    files: [],
  };
  setProductDetailCardHighlight(productId);
  renderProductDetailPanel();

  try {
    const detail = await api(`/api/products/${encodeURIComponent(productId)}`);
    state.productDetail.name = detail.name || productId;
    state.productDetail.source =
      detail.source === "workspace" || String(productId).startsWith("prod_") ? "workspace" : detail.source || "workspace";
    state.productDetail.profile = detail.profile || {};
    state.productDetail.files = detail.files || [];
    state.productDetail.sections = (detail.summary?.sections || []).map((section) => ({
      id: section.id,
      title: section.title,
      body: section.body || "",
    }));
    if (state.productDetail.source !== "workspace") {
      state.productDetail.editing = false;
    } else if (startEditing) {
      state.productDetail.editing = true;
    } else {
      state.productDetail.editing = false;
    }
  } catch (error) {
    showToast(error.message);
    closeProductDetail();
    return;
  } finally {
    state.productDetail.loading = false;
    renderProductDetailPanel();
  }
}

function toggleProductDetailEdit() {
  if (!state.productDetail.productId || state.productDetail.loading) return;
  if (!productDetailCanEdit()) {
    showToast("演示商品暂不可编辑，请先点击「创作」生成本地项目");
    return;
  }
  if (state.productDetail.editing) {
    state.productDetail.editing = false;
    renderProductDetailPanel();
    return;
  }
  state.productDetail.editing = true;
  renderProductDetailPanel();
}

async function saveProductDetail() {
  if (!state.productDetail.productId || !state.productDetail.editing) return;
  if (!productDetailCanEdit()) {
    showToast("演示商品不可保存");
    return;
  }
  const profile = collectProductDetailProfile();
  if (!String(profile.product_name || "").trim()) {
    showToast("请填写商品名称");
    return;
  }
  const sections = collectProductDetailSections();
  try {
    const response = await api(`/api/products/${encodeURIComponent(state.productDetail.productId)}/save`, {
      method: "POST",
      body: JSON.stringify({ profile, sections }),
    });
    state.productDetail.profile = response.profile || profile;
    state.productDetail.name = state.productDetail.profile.product_name || state.productDetail.name;
    state.productDetail.sections = (response.summary?.sections || sections).map((section) => ({
      id: section.id,
      title: section.title,
      body: section.body || "",
    }));
    state.productDetail.files = response.product?.files || state.productDetail.files;
    state.productDetail.editing = false;
    await refreshProductPage();
    setProductDetailCardHighlight(state.productDetail.productId);
    showToast("商品资料已保存");
    renderProductDetailPanel();
  } catch (error) {
    showToast(error.message);
  }
}

function openAddProductModal() {
  const modal = document.getElementById("addProductModal");
  if (!modal) return;
  state.addProductPendingFiles = [];
  renderAddProductFileList();
  modal.classList.add("active");
  document.getElementById("addProductName")?.focus();
}

function closeAddProductModal() {
  document.getElementById("addProductModal")?.classList.remove("active");
  document.getElementById("addProductForm")?.reset();
  state.addProductPendingFiles = [];
  renderAddProductFileList();
  syncProductCategoryCustomVisibility("addProductCatKey", "addProductCatCustomWrap", "addProductCatCustom");
}

function renderAddProductFileList() {
  const host = document.getElementById("addProductFileList");
  if (!host) return;
  const files = state.addProductPendingFiles || [];
  if (!files.length) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = files
    .map(
      (file, index) => `
    <div class="add-product-file-chip">
      <span>${escapeHtml(file.name)}</span>
      <button type="button" class="ui-dismiss-btn ui-dismiss-btn--inline" data-action="remove-add-product-file" data-file-index="${index}" title="移除">×</button>
    </div>`
    )
    .join("");
}

function triggerAddProductFileUpload() {
  document.getElementById("addProductFileInput")?.click();
}

function handleAddProductFileInput(event) {
  const input = event.target;
  const picked = input.files ? Array.from(input.files) : [];
  if (!picked.length) return;
  state.addProductPendingFiles = [...(state.addProductPendingFiles || []), ...picked];
  renderAddProductFileList();
  input.value = "";
}

function removeAddProductPendingFile(index) {
  if (index < 0 || index >= (state.addProductPendingFiles || []).length) return;
  state.addProductPendingFiles.splice(index, 1);
  renderAddProductFileList();
}

async function uploadFilesToProduct(productId, files) {
  if (!productId || !files?.length) return [];
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  const response = await fetch(`/api/products/${encodeURIComponent(productId)}/files`, {
    method: "POST",
    body: formData,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || response.statusText || "上传失败");
  }
  return data.files || [];
}

async function saveProductSummarySections(productId, updates) {
  const detail = await api(`/api/products/${encodeURIComponent(productId)}`);
  const sections = (detail.summary?.sections || []).map((section) => {
    if (Object.prototype.hasOwnProperty.call(updates, section.id)) {
      return { ...section, body: updates[section.id] || "" };
    }
    return section;
  });
  await api(`/api/products/${encodeURIComponent(productId)}/save`, {
    method: "POST",
    body: JSON.stringify({ profile: detail.profile || {}, sections }),
  });
}

async function submitAddProduct() {
  const name = document.getElementById("addProductName")?.value?.trim();
  if (!name) {
    showToast("请填写商品名称");
    return;
  }
  const category = readProductCategoryFields("addProductCatKey", "addProductCatCustom");
  if (!category.product_category_key) {
    showToast("请选择商品类别");
    return;
  }
  if (category.product_category_key === "custom" && !category.product_category) {
    showToast("请填写自定义类别名称");
    syncProductCategoryCustomVisibility("addProductCatKey", "addProductCatCustomWrap", "addProductCatCustom");
    return;
  }
  const payload = {
    product_name: name,
    product_category: category.product_category,
    product_category_key: category.product_category_key,
    product_price: document.getElementById("addProductPrice")?.value?.trim() || "",
    product_brand: document.getElementById("addProductBrand")?.value?.trim() || "",
    product_desc: document.getElementById("addProductDesc")?.value?.trim() || "",
  };
  try {
    const response = await api("/api/products", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const productId = response.product_id;
    const visual = document.getElementById("addProductVisual")?.value?.trim() || "";
    const video = document.getElementById("addProductVideo")?.value?.trim() || "";
    if (visual || video) {
      await saveProductSummarySections(productId, { visual, video });
    }
    const pending = state.addProductPendingFiles || [];
    if (pending.length) {
      await uploadFilesToProduct(productId, pending);
    }
    closeAddProductModal();
    await refreshProductPage();
    await openProductDetail(productId, { editing: true });
    showToast("商品已创建，可继续补充资料与参考资料");
  } catch (error) {
    showToast(error.message);
  }
}

function triggerProductFileUpload() {
  if (!state.productDetail.productId || !state.productDetail.editing) {
    showToast("请先点击「修改」进入编辑模式");
    return;
  }
  document.getElementById("productFileInput")?.click();
}

async function handleProductFileInput(event) {
  const input = event.target;
  const files = input.files;
  if (!files?.length || !state.productDetail.productId) return;
  try {
    state.productDetail.files = await uploadFilesToProduct(state.productDetail.productId, Array.from(files));
    renderProductDetailPanel();
    showToast(`已上传 ${files.length} 个参考资料`);
  } catch (error) {
    showToast(error.message);
  } finally {
    input.value = "";
  }
}

async function deleteProductFile(relativePath) {
  if (!state.productDetail.productId || !relativePath) return;
  if (!window.confirm("确定删除该文件？")) return;
  try {
    const response = await api(
      `/api/products/${encodeURIComponent(state.productDetail.productId)}/files/delete`,
      {
        method: "POST",
        body: JSON.stringify({ relative_path: relativePath }),
      }
    );
    state.productDetail.files = response.files || [];
    renderProductDetailPanel();
    showToast("文件已删除");
  } catch (error) {
    showToast(error.message);
  }
}

async function viewProduct(productId) {
  await openProductDetail(productId);
}

async function createFromProduct(productId) {
  if (productId.startsWith("demo:")) {
    await startDemo(productId.split(":")[1]);
    return;
  }
  if (productId === "bag" || productId === "jeans") {
    await startDemo(productId);
    return;
  }
  await loadProject(productId, { rerunAnalysis: true });
}

function filterProductByCategory(btn, category) {
  state.productCategory = category || "all";
  document.querySelectorAll("#productFilters .filter-btn").forEach((node) => node.classList.remove("active"));
  if (btn) btn.classList.add("active");
  applyProductFilters();
}

function applyProductSearch() {
  const input = document.getElementById("productSearchInput");
  state.productKeyword = (input?.value || "").trim().toLowerCase();
  applyProductFilters();
}

function applyProductFilters() {
  const keyword = state.productKeyword || "";
  const category = state.productCategory || "all";
  let visible = 0;
  document.querySelectorAll("#productGrid .product-card").forEach((card) => {
    const cat = card.getAttribute("data-category") || "all";
    const name = (card.getAttribute("data-name") || "").toLowerCase();
    const matchCategory =
      category === "all" ||
      cat === category ||
      (category === "custom" && cat === "custom");
    const matchKeyword = !keyword || name.includes(keyword);
    const show = matchCategory && matchKeyword;
    card.style.display = show ? "" : "none";
    if (show) visible += 1;
  });
  const countNode = document.getElementById("productTotalCount");
  if (countNode) countNode.textContent = String(visible);
}

function filterProducts(keyword) {
  state.productKeyword = (keyword || "").trim().toLowerCase();
  applyProductFilters();
}

function setProductViewMode(btn, mode) {
  document.querySelectorAll("#page-product .product-view-toggle .view-btn").forEach((node) =>
    node.classList.remove("active")
  );
  if (btn) btn.classList.add("active");
  const grid = document.getElementById("productGrid");
  if (!grid) return;
  grid.classList.toggle("list-view", mode === "list");
}

function bindMaterialFolderHost() {
  const host = document.getElementById("materialUserFolders");
  if (!host || host.dataset.actionsBound === "1") return;
  host.dataset.actionsBound = "1";
  host.addEventListener("click", (event) => {
    const control = event.target.closest("[data-action]");
    if (!control || !host.contains(control)) return;
    const action = control.getAttribute("data-action");
    const folderId = readDataFolderId(control);
    if (!folderId) return;
    event.preventDefault();
    event.stopPropagation();
    if (action === "view-material-folder") {
      void viewMaterialFolderAsync(folderId);
    } else if (action === "add-to-material-folder") {
      void openMaterialFolderAddModal(folderId);
    } else if (action === "delete-material-folder") {
      void deleteMaterialFolder(folderId);
    }
  });
}

function bindActionHandlers() {
  document.body.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    event.preventDefault();
    const action = target.dataset.action;
    if (action === "start-flow" || action === "start-flow-nav") {
      if (action === "start-flow-nav") await startCreativeFlowNav(target);
      else await startCreativeFlow();
      return;
    }
    if (action === "return-home-after-task") {
      returnHomeFromCompletedTask();
      return;
    }
    if (action === "pick-folder") await pickFolder(true);
    if (action === "upload-local-files") triggerLocalFileUpload("task");
    if (action === "open-library-picker") await openLibraryPickerModal("all");
    if (action === "open-library-picker-images") await openLibraryPickerModal("images");
    if (action === "pick-library-folder") {
      setLibraryPickerFolder(target.dataset.folderId || "");
      return;
    }
    if (action === "close-library-picker") closeLibraryPicker();
    if (action === "confirm-library-picker") await confirmLibraryPicker();
    if (action === "upload-library-files") triggerLocalFileUpload("library");
    if (action === "download-library-zip") {
      downloadLibraryZip();
      return;
    }
    if (action === "download-library-material") {
      event.stopPropagation();
      downloadLibraryMaterial(target.dataset.path, target.dataset.name);
      return;
    }
    if (action === "create-material-folder") {
      await createMaterialFolder();
      return;
    }
    if (action === "view-material-folder") {
      event.stopPropagation();
      const folderId = readDataFolderId(target);
      void viewMaterialFolderAsync(folderId);
      return;
    }
    if (action === "add-to-material-folder") {
      event.stopPropagation();
      const folderId = readDataFolderId(target);
      void openMaterialFolderAddModal(folderId);
      return;
    }
    if (action === "delete-material-folder") {
      event.stopPropagation();
      const folderId = readDataFolderId(target);
      void deleteMaterialFolder(folderId);
      return;
    }
    if (action === "clear-material-folder-view") {
      clearMaterialFolderView();
      return;
    }
    if (action === "cancel-delete-material-folder") {
      closeMaterialFolderDeleteModal();
      return;
    }
    if (action === "confirm-delete-material-folder") {
      await confirmMaterialFolderDelete();
      return;
    }
    if (action === "close-material-folder-add") {
      closeMaterialFolderAddModal();
      return;
    }
    if (action === "confirm-material-folder-add") {
      await confirmMaterialFolderAdd();
      return;
    }
    if (action === "pick-library-files") await pickLocalFiles("library");
    if (action === "import-library-folder") await pickFolder(false, "library");
    if (action === "show-material-page") showNavPage("material");
    if (action === "open-image-picker") await openImagePickerModal("step1");
    if (action === "open-image-picker-step4") await openImagePickerModal("step4");
    if (action === "delete-task-material") {
      event.stopPropagation();
      await deleteTaskMaterial(target.dataset.path);
      return;
    }
    if (action === "delete-library-material") {
      event.stopPropagation();
      await deleteLibraryMaterial(target.dataset.path);
      return;
    }
    if (action === "rename-library-material") {
      event.stopPropagation();
      renameLibraryMaterial(target.dataset.path, target.dataset.name);
      return;
    }
    if (action === "cancel-material-rename") {
      closeMaterialRenameModal();
      return;
    }
    if (action === "confirm-material-rename") {
      await confirmMaterialRename();
      return;
    }
    if (action === "delete-product") {
      event.stopPropagation();
      await deleteProduct(target.dataset.productId);
      return;
    }
    if (action === "view-product-detail") {
      await openProductDetail(target.dataset.productId, { editing: false });
      return;
    }
    if (action === "edit-product-detail-open") {
      await openProductDetail(target.dataset.productId, { editing: true });
      return;
    }
    if (action === "close-product-detail") {
      closeProductDetail();
      return;
    }
    if (action === "edit-product-detail") {
      toggleProductDetailEdit();
      return;
    }
    if (action === "save-product-detail") {
      await saveProductDetail();
      return;
    }
    if (action === "open-add-product") openAddProductModal();
    if (action === "close-add-product") closeAddProductModal();
    if (action === "upload-add-product-files") triggerAddProductFileUpload();
    if (action === "remove-add-product-file") {
      removeAddProductPendingFile(Number(target.dataset.fileIndex));
      return;
    }
    if (action === "submit-add-product") await submitAddProduct();
    if (action === "start-creative-task") startCreativeFlow();
    if (action === "upload-product-files") triggerProductFileUpload();
    if (action === "delete-product-file") await deleteProductFile(target.dataset.relativePath);
    if (action === "view-history-detail") await openHistoryDetail(target.dataset.taskId);
    if (action === "close-history-detail") closeHistoryDetail();
    if (action === "delete-history") {
      event.stopPropagation();
      await deleteHistoryItem(target.dataset.taskId);
      return;
    }
    if (action === "apply-product-search") {
      applyProductSearch();
      return;
    }
    if (action === "remove-reference-image") await removeReferenceImage(target.dataset.path);
    if (action === "generate-image-prompts") await generateImagePrompts();
    if (action === "generate-images") await generateImages();
    if (action === "generate-video") await generateVideoOnly();
    if (action === "close-image-picker") closeImagePicker(true);
    if (action === "confirm-image-picker") await confirmImagePicker();
    if (action === "pick-demo") await pickDemo(target.dataset.demo);
    if (action === "go-step-1") goToStep1();
    if (action === "go-step-2") await goToStep2();
    if (action === "go-step-3") goToStep3();
    if (action === "go-step-4") await goToStep4();
    if (action === "go-step-5") goToStep5();
    if (action === "show-ai-settings") await showAISettings();
    if (action === "close-ai-settings") closeAISettings();
    if (action === "select-ai-capability") selectAICapability(target.dataset.capability);
    if (action === "save-ai-settings") await saveAISettings();
    if (action === "test-local-connection") await testLocalConnection();
    if (action === "show-help") showHelp();
    if (action === "close-help-center") closeHelpCenter();
    if (action === "help-center-tab") setHelpCenterTab(target.dataset.sectionId);
    if (action === "watch-demo") watchDemo();
    if (action === "show-projects") showNavPage("product");
    if (action === "edit-understanding") await editUnderstandingNotes();
    if (action === "regenerate-directions") await regenerateDirections();
    if (action === "regenerate-content") await regenerateContent();
    if (action === "add-product") startCreativeFlow();
    if (action === "start-creative-task") startCreativeFlow();
  });
}

function initScrollAnimations() {
  const animatedElements = document.querySelectorAll(".feature-card, .project-card");
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.style.opacity = "1";
        entry.target.style.transform = "translateY(0)";
        observer.unobserve(entry.target);
      });
    },
    { threshold: 0.1, rootMargin: "0px 0px -50px 0px" }
  );
  animatedElements.forEach((el) => {
    el.style.opacity = "0";
    el.style.transform = "translateY(30px)";
    el.style.transition = "opacity 0.6s ease, transform 0.6s ease";
    observer.observe(el);
  });
}

async function bootstrap() {
  bindActionHandlers();
  bindMaterialFolderHost();
  bindProductFormInputs();
  document.getElementById("productSearchInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyProductSearch();
    }
  });
  document.getElementById("productFileInput")?.addEventListener("change", handleProductFileInput);
  document.getElementById("addProductFileInput")?.addEventListener("change", handleAddProductFileInput);
  document.getElementById("productDetailModal")?.addEventListener("click", (event) => {
    if (event.target.id === "productDetailModal" || event.target.classList.contains("product-detail-modal-backdrop")) {
      closeProductDetail();
    }
  });
  document.getElementById("addProductModal")?.addEventListener("click", (event) => {
    if (event.target.id === "addProductModal") closeAddProductModal();
  });
  document.getElementById("aiSettingsForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    void saveAISettings();
  });
  ["aiEnabled", "aiLocation", "aiCredentialAction"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", syncAISettingsFields);
  });
  document.getElementById("aiSettingsModal")?.addEventListener("click", (event) => {
    if (event.target.id === "aiSettingsModal") closeAISettings();
  });
  document.getElementById("helpCenterModal")?.addEventListener("click", (event) => {
    if (event.target.id === "helpCenterModal") closeHelpCenter();
  });
  document.getElementById("materialRenameModal")?.addEventListener("click", (event) => {
    if (event.target.id === "materialRenameModal") closeMaterialRenameModal();
  });
  document.getElementById("materialRenameInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void confirmMaterialRename();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAISettings();
      closeHelpCenter();
      closeMaterialRenameModal();
      if (document.getElementById("productDetailModal")?.classList.contains("active")) {
        closeProductDetail();
      }
    }
  });

  const health = await api("/api/health");
  if (!health.api_version || !String(health.api_version).includes("materials-v2")) {
    showToast("检测到旧版后端，请关闭网站窗口后重新运行 启动网站.bat");
  }
  const meta = await api("/api/meta");
  state.demoPacks = meta.demo_packs || [];
  state.help = meta.help || null;
  await refreshProjects();

  const saved = await api("/api/state");
  if (saved.material_path) {
    state.materialPath = saved.material_path;
    try {
      const preview = await api("/api/material-preview", {
        method: "POST",
        body: JSON.stringify({ material_path: saved.material_path }),
      });
      applyCatalog(preview);
    } catch (_) {
      /* ignore */
    }
  }
  if (saved.product_form) {
    applyProductForm(saved.product_form);
  }
  state.understandingNotes = saved.understanding_notes || "";
  if (saved.analysis) {
    state.analysis = saved.analysis;
    if (saved.analysis.product_form) {
      state.productForm = { ...saved.analysis.product_form };
    }
    const first = saved.analysis.directions?.directions?.[0];
    state.selectedDirectionId = first?.direction_id || null;
    renderUnderstanding();
    renderDirections();
  }
  if (saved.publish) {
    state.publish = saved.publish;
    renderContent();
    renderExportInfo();
  } else if (saved.generated_media?.images?.length) {
    renderGeneratedMedia();
    renderStep4ReferenceImages();
  }
  state.imagePrompts = saved.image_prompts || [];
  state.generatedMedia = saved.generated_media || {};
  if (saved.selected_direction_id) state.selectedDirectionId = saved.selected_direction_id;
  if (saved.catalog) applyCatalog(saved.catalog);
  if (saved.reference_images?.length) {
    state.selectedReferenceImages = saved.reference_images;
    state.imagesConfirmed = true;
    state.materialsReady = true;
    renderUploadPreview();
  } else if (countMaterialFiles() > 0) {
    state.materialsReady = true;
  }

  await refreshProductPage();
  await refreshHistoryPage();
  initScrollAnimations();
}

document.addEventListener("DOMContentLoaded", () => {
  bootstrap().catch((error) => showToast(error.message));
});
