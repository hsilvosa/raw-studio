const $ = id => document.getElementById(id);

let images = [], profiles = [], current = null, chosen = null, busy = false;
const results = new Map(), selected = new Set();
const selectedProfiles = new Set();

// --- API & Tasks ---
async function api(path, body) {
  const r = await fetch('/api' + path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  const v = await r.json();
  if (!r.ok) throw Error(typeof v.detail === 'string' ? v.detail : JSON.stringify(v.detail));
  return v;
}

function report(text) {
  $('status').textContent = text;
}

function buttons() {
  const hasProfile = selectedProfiles.size > 0;
  $('develop').disabled = busy || !current || !hasProfile;
  $('all').disabled = busy || !current || profiles.length === 0;
  $('export').disabled = busy || !chosen;
  $('import').disabled = busy;
  $('clear-cache').disabled = busy;
  if (selectedProfiles.size === 1 && [...selectedProfiles][0] === '00_PROMPT_IA') {
    $('develop').textContent = 'Develop AI prompt style';
  } else {
    $('develop').textContent = selectedProfiles.size > 1 ? `Develop ${selectedProfiles.size} profiles` : 'Develop profile';
  }
}

async function waitJob(id, prefix = '') {
  for (;;) {
    const j = await api('/jobs/' + id);
    report(prefix ? `${prefix} — ${j.message}` : j.message);
    if (j.state === 'done') return j.result;
    if (j.state === 'error') throw Error(j.message);
    await new Promise(r => setTimeout(r, 1200));
  }
}

async function task(fn) {
  if (busy) return;
  busy = true;
  buttons();
  try {
    await fn();
  } catch (e) {
    report(e.message);
  } finally {
    busy = false;
    buttons();
  }
}

// --- Zoom & Pan Engine ---
let zoomMode = 'fit'; // 'fit' | 'manual'
let zoomScale = 1.0;
let fitScale = 1.0;
let panX = 0, panY = 0;
let naturalWidth = 1600, naturalHeight = 1067;
let isDragging = false, lastDragX = 0, lastDragY = 0;

const vpBefore = $('viewport-before');
const vpAfter = $('viewport-after');
const viewports = [vpBefore, vpAfter];

function getActiveVpRect() {
  const r = vpBefore.getBoundingClientRect();
  return (r.width > 0 && r.height > 0) ? r : vpAfter.getBoundingClientRect();
}

function updateZoomTransform() {
  const r = getActiveVpRect();
  if (!r || r.width <= 0 || r.height <= 0 || naturalWidth <= 0 || naturalHeight <= 0) return;

  fitScale = Math.min((r.width - 8) / naturalWidth, (r.height - 8) / naturalHeight);

  if (zoomMode === 'fit') {
    zoomScale = fitScale;
    panX = (r.width - naturalWidth * zoomScale) / 2;
    panY = (r.height - naturalHeight * zoomScale) / 2;
    $('fit').classList.add('active');
    $('zoom-100').classList.remove('active');
    $('zoom-level').textContent = 'Fit';
    vpBefore.classList.remove('can-pan');
    vpAfter.classList.remove('can-pan');
  } else {
    $('fit').classList.remove('active');
    const pct = Math.round(zoomScale * 100);
    $('zoom-level').textContent = pct + '%';
    $('zoom-100').classList.toggle('active', Math.abs(pct - 100) < 2);
    vpBefore.classList.add('can-pan');
    vpAfter.classList.add('can-pan');
  }

  const transformStyle = `translate3d(${panX}px, ${panY}px, 0px) scale(${zoomScale})`;
  for (const imgId of ['before', 'after', 'mask-overlay']) {
    const img = $(imgId);
    if (img) {
      img.style.width = naturalWidth + 'px';
      img.style.height = naturalHeight + 'px';
      img.style.transform = transformStyle;
    }
  }
}

function zoomAt(factor, clientX, clientY, sourceVp) {
  const r = (sourceVp || vpBefore).getBoundingClientRect();
  const vx = clientX - r.left;
  const vy = clientY - r.top;

  const minScale = Math.min(0.1, fitScale * 0.5);
  const maxScale = 5.0;
  const targetScale = Math.min(Math.max(zoomScale * factor, minScale), maxScale);

  if (Math.abs(targetScale - zoomScale) < 0.001) return;

  const ratio = targetScale / zoomScale;
  panX = vx - (vx - panX) * ratio;
  panY = vy - (vy - panY) * ratio;
  zoomScale = targetScale;
  zoomMode = 'manual';
  updateZoomTransform();
}

function zoomAtCenter(factor) {
  const r = getActiveVpRect();
  zoomAt(factor, r.left + r.width / 2, r.top + r.height / 2, vpBefore);
}

function setZoom100() {
  const r = getActiveVpRect();
  zoomScale = 1.0;
  zoomMode = 'manual';
  panX = (r.width - naturalWidth * zoomScale) / 2;
  panY = (r.height - naturalHeight * zoomScale) / 2;
  updateZoomTransform();
}

// Attach zoom and pan event listeners to viewports
for (const vp of viewports) {
  vp.addEventListener('wheel', e => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.18 : (1 / 1.18);
    zoomAt(factor, e.clientX, e.clientY, vp);
  }, { passive: false });

  vp.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    isDragging = true;
    lastDragX = e.clientX;
    lastDragY = e.clientY;
    vpBefore.classList.add('panning');
    vpAfter.classList.add('panning');
    e.preventDefault();
  });

  vp.addEventListener('dblclick', e => {
    if (zoomMode === 'fit') {
      zoomAt(1.0 / fitScale, e.clientX, e.clientY, vp);
    } else {
      zoomMode = 'fit';
      updateZoomTransform();
    }
  });
}

window.addEventListener('mousemove', e => {
  if (!isDragging) return;
  const dx = e.clientX - lastDragX;
  const dy = e.clientY - lastDragY;
  lastDragX = e.clientX;
  lastDragY = e.clientY;
  panX += dx;
  panY += dy;
  zoomMode = 'manual';
  updateZoomTransform();
});

window.addEventListener('mouseup', () => {
  if (isDragging) {
    isDragging = false;
    vpBefore.classList.remove('panning');
    vpAfter.classList.remove('panning');
  }
});

// Window and viewport resize observer
const resizeObserver = new ResizeObserver(() => {
  if (zoomMode === 'fit') {
    updateZoomTransform();
  }
});
resizeObserver.observe(vpBefore);
window.addEventListener('resize', () => {
  if (zoomMode === 'fit') updateZoomTransform();
});

// Image load handlers to grab natural resolution
$('before').onload = function() {
  if (this.naturalWidth && this.naturalHeight) {
    naturalWidth = this.naturalWidth;
    naturalHeight = this.naturalHeight;
  }
  updateZoomTransform();
};

$('after').onload = function() {
  if (this.naturalWidth && this.naturalHeight) {
    naturalWidth = this.naturalWidth;
    naturalHeight = this.naturalHeight;
  }
  updateZoomTransform();
};

// Toolbar buttons
$('fit').onclick = () => {
  zoomMode = 'fit';
  updateZoomTransform();
};
$('zoom-out').onclick = () => zoomAtCenter(1 / 1.25);
$('zoom-in').onclick = () => zoomAtCenter(1.25);
$('zoom-100').onclick = () => setZoom100();

// --- Full Page View Mode ---
let isFullPage = false;
function toggleFullPage(forceState) {
  isFullPage = forceState !== undefined ? forceState : !isFullPage;
  document.body.classList.toggle('full-page-active', isFullPage);
  $('full-page').classList.toggle('active', isFullPage);
  $('full-page').title = isFullPage ? 'Exit full page view (Esc)' : 'Toggle full page view (F / Esc)';
  setTimeout(updateZoomTransform, 60);
}
$('full-page').onclick = () => toggleFullPage();

// Horizontal mouse wheel scrolling and drag-to-scroll for developed profiles bar
const resultsBar = $('results');
let isResultsDragging = false;
let resultsStartX = 0;
let resultsScrollLeft = 0;
let resultsHasDragged = false;

if (resultsBar) {
  resultsBar.addEventListener('wheel', (e) => {
    const rawDelta = Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
    if (rawDelta !== 0) {
      e.preventDefault();
      // Accelerated multiplier (3.2x) so scrolling left-to-right is swift and responsive
      resultsBar.scrollLeft += rawDelta * 3.2;
    }
  }, { passive: false });

  resultsBar.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    isResultsDragging = true;
    resultsHasDragged = false;
    resultsStartX = e.pageX - resultsBar.offsetLeft;
    resultsScrollLeft = resultsBar.scrollLeft;
    resultsBar.classList.add('dragging');
  });

  window.addEventListener('mousemove', (e) => {
    if (!isResultsDragging) return;
    const x = e.pageX - resultsBar.offsetLeft;
    const walk = (x - resultsStartX) * 1.6;
    if (Math.abs(walk) > 4) resultsHasDragged = true;
    resultsBar.scrollLeft = resultsScrollLeft - walk;
  });

  window.addEventListener('mouseup', () => {
    if (isResultsDragging) {
      isResultsDragging = false;
      resultsBar.classList.remove('dragging');
    }
  });
}

// Keyboard shortcuts for zooming and full page
window.addEventListener('keydown', e => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
  if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomAtCenter(1.25); }
  else if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomAtCenter(1 / 1.25); }
  else if (e.key === '0') { e.preventDefault(); zoomMode = 'fit'; updateZoomTransform(); }
  else if (e.key === '1') { e.preventDefault(); setZoom100(); }
  else if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    toggleFullPage();
  }
  else if (e.key === 'Escape' && isFullPage) {
    const isModalOpen = $('browser')?.open || $('profile-info-dialog')?.open;
    if (!isModalOpen) {
      e.preventDefault();
      toggleFullPage(false);
    }
  }
});

// --- Library Expand / Grid View Toggle ---
$('toggle-library').onclick = () => {
  const main = $('main');
  main.classList.toggle('library-expanded');
  const isExpanded = main.classList.contains('library-expanded');
  $('toggle-library').textContent = isExpanded ? '⊟ Collapse' : '⛶ Expand';
  $('toggle-library').title = isExpanded ? 'Collapse library to list view' : 'Expand library to grid view';
  setTimeout(updateZoomTransform, 220);
};

// --- Library Selection & Photo Deletion ---
let librarySelecting = false;
const selectedLibraryImages = new Set();

function setLibrarySelecting(enabled) {
  librarySelecting = enabled;
  $('library').classList.toggle('selecting', enabled);
  $('library-selection-bar').hidden = !enabled;
  $('lib-select-mode').textContent = enabled ? 'Done' : 'Select';
  if (!enabled) selectedLibraryImages.clear();
  updateLibrarySelectionUI();
  renderLibraryItems();
}

function updateLibrarySelectionUI() {
  const count = selectedLibraryImages.size;
  $('lib-delete-btn').textContent = `Delete (${count})`;
  $('lib-delete-btn').disabled = count === 0 || busy;
}

function deletePhotos(imageIds) {
  const count = imageIds.length;
  if (!count) return;
  const msg = count === 1
    ? 'Remove this photo from your library?\n\n(Your original RAW file on disk will NOT be deleted).'
    : `Remove ${count} photos from your library?\n\n(Your original RAW files on disk will NOT be deleted).`;
  if (!confirm(msg)) return;

  task(async () => {
    report(`Removing ${count} photo${count > 1 ? 's' : ''}...`);
    await api('/images/delete', { image_ids: imageIds });
    for (const id of imageIds) {
      results.delete(id);
      selectedLibraryImages.delete(id);
    }
    const currentWasDeleted = current && imageIds.includes(current.id);
    if (currentWasDeleted) {
      current = null;
      chosen = null;
    }
    await refresh();
    if (!current) {
      if (images.length > 0) {
        select(images[0]);
      } else {
        $('empty').hidden = false;
        $('pair').hidden = true;
        $('filename').textContent = 'Select a photograph';
        $('results').replaceChildren();
        buttons();
      }
    }
    updateLibrarySelectionUI();
    report(`Removed ${count} photo${count > 1 ? 's' : ''} from library.`);
  });
}

$('lib-select-mode').onclick = () => setLibrarySelecting(!librarySelecting);
$('lib-cancel').onclick = () => setLibrarySelecting(false);
$('lib-select-all').onclick = () => {
  for (const im of images) selectedLibraryImages.add(im.id);
  renderLibraryItems();
  updateLibrarySelectionUI();
};
$('lib-select-none').onclick = () => {
  selectedLibraryImages.clear();
  renderLibraryItems();
  updateLibrarySelectionUI();
};
$('lib-delete-btn').onclick = () => {
  if (selectedLibraryImages.size > 0) deletePhotos([...selectedLibraryImages]);
};

function renderLibraryItems() {
  $('images').replaceChildren();
  for (const im of images) {
    const isLibSel = selectedLibraryImages.has(im.id);
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'photo' + (current?.id === im.id ? ' active' : '') + (isLibSel ? ' lib-selected' : '');

    // Multi-select checkbox
    if (librarySelecting) {
      const check = document.createElement('input');
      check.type = 'checkbox';
      check.className = 'photo-check';
      check.checked = isLibSel;
      check.onclick = (e) => {
        e.stopPropagation();
        if (check.checked) selectedLibraryImages.add(im.id);
        else selectedLibraryImages.delete(im.id);
        b.classList.toggle('lib-selected', check.checked);
        updateLibrarySelectionUI();
      };
      b.append(check);
    } else {
      // Individual quick delete button on hover
      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'photo-delete-btn';
      delBtn.textContent = '✕';
      delBtn.title = 'Remove photo from library';
      delBtn.onclick = (e) => {
        e.stopPropagation();
        deletePhotos([im.id]);
      };
      b.append(delBtn);
    }

    const img = new Image();
    img.src = '/api/images/' + im.id + '/preview';
    img.alt = im.name;
    const nameSpan = document.createElement('span');
    nameSpan.className = 'photo-name';
    nameSpan.textContent = im.name;
    b.append(img, nameSpan);

    b.onclick = () => {
      if (librarySelecting) {
        const next = !selectedLibraryImages.has(im.id);
        if (next) selectedLibraryImages.add(im.id);
        else selectedLibraryImages.delete(im.id);
        b.classList.toggle('lib-selected', next);
        const chk = b.querySelector('.photo-check');
        if (chk) chk.checked = next;
        updateLibrarySelectionUI();
      } else {
        if (!busy) select(im);
      }
    };
    $('images').append(b);
  }
}

// --- Library & Results Management ---
async function refresh() {
  images = await api('/images');
  $('count').textContent = images.length;
  renderLibraryItems();
  if (!current && images.length) select(images[0]);
}


function select(im) {
  current = im;
  chosen = null;
  $('filename').textContent = im.name;
  $('empty').hidden = true;
  $('pair').hidden = false;
  $('before').src = '/api/images/' + im.id + '/preview';
  $('after').src = $('before').src;
  $('after-label').textContent = 'NO PROFILE';
  $('reason').textContent = 'Select a profile and click Develop.';
  $('recipe').textContent = '';
  drawResults();
  buttons();
  zoomMode = 'fit';
  updateZoomTransform();
  for (const b of $('images').children) {
    const nameEl = b.querySelector('.photo-name');
    b.classList.toggle('active', (nameEl ? nameEl.textContent : b.textContent) === im.name);
  }
}

function showResult(result) {
  chosen = result;
  $('after').src = result.url;
  const hasModel = Boolean(result.recipe?.model || result.recipe?.proposal);
  $('after-label').textContent = result.recipe.profile_title + (hasModel ? ' · [IA]' : '');
  const warnings = result.recipe.warnings && result.recipe.warnings.length ? ' ' + result.recipe.warnings.join(' ') : '';
  $('reason').textContent = (result.recipe.reason || '') + warnings;
  $('recipe').textContent = JSON.stringify(result.recipe.stack, null, 2);
  for (const b of $('results').children) {
    const titleEl = b.querySelector('.result-title');
    const isActive = (titleEl ? titleEl.textContent : b.textContent).includes(result.recipe.profile_title);
    b.classList.toggle('active', isActive);
    if (isActive) {
      b.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }

  buttons();
  updateZoomTransform();
}

function drawResults() {
  $('results').replaceChildren();
  for (const r of results.get(current?.id) || []) {
    const b = document.createElement('button');
    const hasModel = Boolean(r.recipe?.model || r.recipe?.proposal);
    b.className = 'result' + (chosen?.render_id === r.render_id ? ' active' : '') + (hasModel ? ' has-model' : '');

    const thumbWrap = document.createElement('div');
    thumbWrap.className = 'result-thumb-wrap';
    const img = new Image();
    img.src = r.url;
    img.alt = r.recipe.profile_title;
    thumbWrap.append(img);

    if (hasModel) {
      const badge = document.createElement('span');
      badge.className = 'model-badge';
      badge.textContent = 'IA';
      badge.title = 'Adapted with local model';
      thumbWrap.append(badge);
    }

    const title = document.createElement('span');
    title.className = 'result-title';
    title.textContent = r.recipe.profile_title;
    if (hasModel) {
      const tag = document.createElement('span');
      tag.className = 'result-model-tag';
      tag.textContent = 'IA';
      title.prepend(tag);
    }

    b.append(thumbWrap, title);
    b.onclick = (e) => {
      if (resultsHasDragged) {
        e.preventDefault();
        return;
      }
      showResult(r);
    };
    $('results').append(b);
  }
}

// --- Profile Multi-Select Management ---
function updateProfileSelectionUI() {
  const count = selectedProfiles.size;
  $('profile-selected-badge').textContent = count === 1 ? '1 selected' : `${count} selected`;
  if (count === 0) {
    $('profile-summary').textContent = 'Select profiles...';
  } else if (count === 1) {
    const pid = [...selectedProfiles][0];
    const found = profiles.find(p => p.id === pid);
    $('profile-summary').textContent = found ? found.title : pid;
    if (pid === '00_PROMPT_IA') {
      $('model').checked = true;
    }
  } else {
    $('profile-summary').textContent = `${count} profiles selected`;
    if (selectedProfiles.has('00_PROMPT_IA')) {
      $('model').checked = true;
    }
  }
  buttons();
}

function initProfileSelector() {
  $('profile-options').replaceChildren();
  if (profiles.length && selectedProfiles.size === 0) {
    selectedProfiles.add(profiles[0].id);
  }

  for (const p of profiles) {
    const label = document.createElement('label');
    label.className = 'profile-option';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.value = p.id;
    check.checked = selectedProfiles.has(p.id);
    check.onchange = () => {
      if (check.checked) selectedProfiles.add(p.id);
      else selectedProfiles.delete(p.id);
      updateProfileSelectionUI();
    };
    const titleSpan = document.createElement('span');
    titleSpan.className = 'profile-option-title';
    titleSpan.textContent = p.title;
    label.append(check, titleSpan);
    $('profile-options').append(label);
  }

  $('profile-toggle').onclick = (e) => {
    e.stopPropagation();
    const isHidden = $('profile-dropdown').hidden;
    $('profile-dropdown').hidden = !isHidden;
    $('profile-toggle').classList.toggle('open', isHidden);
    if (isHidden) $('profile-search').focus();
  };

  document.addEventListener('click', (e) => {
    if (!$('profile-multiselect').contains(e.target)) {
      $('profile-dropdown').hidden = true;
      $('profile-toggle').classList.remove('open');
    }
  });

  $('select-all-profiles').onclick = (e) => {
    e.stopPropagation();
    for (const p of profiles) selectedProfiles.add(p.id);
    for (const c of $('profile-options').querySelectorAll('input[type=checkbox]')) c.checked = true;
    updateProfileSelectionUI();
  };

  $('select-none-profiles').onclick = (e) => {
    e.stopPropagation();
    selectedProfiles.clear();
    for (const c of $('profile-options').querySelectorAll('input[type=checkbox]')) c.checked = false;
    updateProfileSelectionUI();
  };

  $('profile-search').oninput = () => {
    const q = $('profile-search').value.toLowerCase();
    for (const opt of $('profile-options').children) {
      const text = opt.textContent.toLowerCase();
      opt.style.display = text.includes(q) ? '' : 'none';
    }
  };

  // Profile guide dialog
  $('profile-info-btn').onclick = () => {
    $('profile-info-list').replaceChildren();
    for (const p of profiles) {
      const card = document.createElement('div');
      card.className = 'profile-info-card';
      const strong = document.createElement('strong');
      strong.textContent = p.title;
      const desc = document.createElement('p');
      desc.textContent = p.description;
      card.append(strong, desc);
      $('profile-info-list').append(card);
    }
    $('profile-info-dialog').showModal();
  };

  $('close-profile-info').onclick = () => {
    $('profile-info-dialog').close();
  };

  updateProfileSelectionUI();
}

// --- Develop & Export Actions ---
async function develop(profileId, currentNum = 1, total = 1, remaining = 0, profileTitle = '') {
  const prefix = total > 1 ? `[${currentNum}/${total}] (${remaining} remaining) ${profileTitle}` : '';
  if (prefix) report(`${prefix} — Preparing image`);
  const isPromptProfile = (profileId === '00_PROMPT_IA');
  const userIntent = ($('intent').value || '').trim();
  const j = await api('/develop', {
    image_id: current.id,
    profile_id: profileId,
    intensity: Number($('intensity').value) / 100,
    exposure_offset: Number($('exposure').value),
    adapt: $('adapt').checked,
    use_model: $('model').checked || isPromptProfile,
    intent: userIntent || (isPromptProfile ? 'Estilo fotográfico cinematográfico y armónico con paleta rica y tonos cuidados.' : 'Adapt style to the scene and preserve whites.')
  });
  const r = await waitJob(j.job_id, prefix);
  const items = results.get(current.id) || [];
  items.push(r);
  results.set(current.id, items);
  drawResults();
  showResult(r);
}

async function developBatch(profileIds) {
  const total = profileIds.length;
  for (let i = 0; i < total; i++) {
    const pid = profileIds[i];
    const p = profiles.find(item => item.id === pid);
    const currentNum = i + 1;
    const remaining = total - currentNum;
    await develop(pid, currentNum, total, remaining, p ? p.title : pid);
  }
  report(`Finished developing ${total} profile${total > 1 ? 's' : ''}.`);
}

$('develop').onclick = () => task(() => developBatch([...selectedProfiles]));
$('all').onclick = () => task(() => developBatch(profiles.filter(p => p.id !== '00_PROMPT_IA').map(p => p.id)));

$('export').onclick = () => task(async () => {
  const j = await api('/renders/' + chosen.render_id + '/export', {});
  const r = await waitJob(j.job_id);
  report('Saved to ' + r.path);
});

$('clear-cache').onclick = () => task(async () => {
  if (!confirm('Are you sure you want to clear all developed photo caches?')) return;
  const res = await api('/renders/clear', {});
  results.clear();
  chosen = null;
  $('results').replaceChildren();
  if (current) {
    $('after').src = $('before').src;
    $('after-label').textContent = 'NO PROFILE';
    $('reason').textContent = 'Select a profile and click Develop.';
    $('recipe').textContent = '';
  }
  buttons();
  report(res.message || 'Cache cleared.');
});

$('intensity').oninput = () => $('intensity-value').textContent = $('intensity').value + ' %';
$('exposure').oninput = () => $('exposure-value').textContent = $('exposure').value + ' EV';

// --- Folder Import Browser ---
let currentBrowseData = null;
let currentBrowseFilter = '';
const browserKnownFiles = new Map();

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || isNaN(bytes)) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function updateBrowserSelectionSummary() {
  $('selected-count').textContent = selected.size + ' selected';
  let totalBytes = 0;
  for (const path of selected) {
    const f = browserKnownFiles.get(path);
    if (f && f.size) totalBytes += f.size;
  }
  $('selected-details').textContent = totalBytes > 0 ? `(${formatBytes(totalBytes)})` : '';
  $('confirm-import').disabled = selected.size === 0;
}

function renderBrowserFiles() {
  if (!currentBrowseData) return;
  const grid = $('browser-grid');
  grid.replaceChildren();

  const query = currentBrowseFilter.toLowerCase().trim();
  const fileItems = currentBrowseData.items.filter(item => !item.directory);
  const matchedFiles = query
    ? fileItems.filter(item => item.name.toLowerCase().includes(query) || (item.extension && item.extension.toLowerCase().includes(query)))
    : fileItems;

  $('browser-empty').hidden = matchedFiles.length > 0;

  for (const f of matchedFiles) {
    browserKnownFiles.set(f.path, f);
    const card = document.createElement('div');
    card.className = 'browser-card' + (selected.has(f.path) ? ' selected' : '');

    // Checkbox
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'browser-check';
    check.checked = selected.has(f.path);
    check.onclick = (e) => {
      e.stopPropagation();
      toggleCardSelection(f, card, check, check.checked);
    };

    // Thumbnail
    const wrap = document.createElement('div');
    wrap.className = 'browser-thumb-wrap';

    const img = document.createElement('img');
    img.className = 'browser-thumb';
    img.loading = 'lazy';
    img.src = '/api/browse/thumbnail?path=' + encodeURIComponent(f.path);
    img.alt = f.name;
    img.onerror = () => {
      img.style.display = 'none';
      const placeholder = document.createElement('span');
      placeholder.className = 'browser-thumb-placeholder';
      placeholder.textContent = 'No preview';
      wrap.append(placeholder);

    };

    const badge = document.createElement('span');
    badge.className = 'browser-badge' + (f.is_raw ? ' raw' : '');
    badge.textContent = f.is_raw ? 'RAW' : (f.extension ? f.extension.slice(1).toUpperCase() : 'IMG');

    wrap.append(img, badge);

    // Metadata info
    const info = document.createElement('div');
    info.className = 'browser-card-info';

    const name = document.createElement('span');
    name.className = 'browser-card-name';
    name.textContent = f.name;
    name.title = f.name;

    const size = document.createElement('span');
    size.className = 'browser-card-size';
    size.textContent = formatBytes(f.size);

    info.append(name, size);

    card.append(check, wrap, info);

    card.onclick = () => {
      const next = !selected.has(f.path);
      check.checked = next;
      toggleCardSelection(f, card, check, next);
    };

    grid.append(card);
  }
}

function toggleCardSelection(f, card, check, isChecked) {
  if (isChecked) {
    selected.add(f.path);
    card.classList.add('selected');
  } else {
    selected.delete(f.path);
    card.classList.remove('selected');
  }
  updateBrowserSelectionSummary();
}

async function browse(path = '') {
  try {
    const data = await api('/browse?path=' + encodeURIComponent(path));
    currentBrowseData = data;

    // Path input & parent
    $('browser-path-input').value = data.path;
    $('parent').disabled = !data.parent;
    $('parent').onclick = () => browse(data.parent);

    // Drives list
    const drivesBox = $('browser-drives');
    drivesBox.replaceChildren();
    for (const drive of data.drives || []) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'drive-chip' + (data.path.toLowerCase().startsWith(drive.toLowerCase()) ? ' active' : '');
      btn.textContent = drive;
      btn.onclick = () => browse(drive);
      drivesBox.append(btn);
    }

    // Default folder button
    $('browser-default-btn').onclick = () => browse(data.default_root);
    $('browser-default-btn').classList.toggle('active', data.is_default);

    // Set as default folder button
    $('browser-set-default-btn').onclick = async () => {
      try {
        const res = await api('/browse/set-default', { path: data.path });
        report(res.message || 'Default folder updated');
        data.default_root = res.default_root;
        data.is_default = true;
        $('browser-default-btn').classList.add('active');
      } catch (e) {
        report(e.message);
      }
    };

    // Subfolders list
    const subfolders = data.items.filter(i => i.directory);
    const subfolderSection = $('browser-folders-section');
    const foldersList = $('browser-folders');
    foldersList.replaceChildren();
    if (subfolders.length > 0) {
      subfolderSection.hidden = false;
      for (const dir of subfolders) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'folder-pill';
        btn.textContent = dir.name + ' /';
        btn.onclick = () => browse(dir.path);

        foldersList.append(btn);
      }
    } else {
      subfolderSection.hidden = true;
    }

    // Render photo cards
    renderBrowserFiles();
    updateBrowserSelectionSummary();
  } catch (e) {
    report(e.message);
  }
}

// Navigation Events
$('browser-go').onclick = () => {
  const target = $('browser-path-input').value.trim();
  if (target) browse(target);
};

$('browser-path-input').onkeydown = (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const target = $('browser-path-input').value.trim();
    if (target) browse(target);
  }
};

$('browser-refresh').onclick = () => {
  if (currentBrowseData) browse(currentBrowseData.path);
  else browse();
};

// Filter input
$('browser-filter').oninput = (e) => {
  currentBrowseFilter = e.target.value;
  renderBrowserFiles();
};

// Selection toolbar
$('select-all').onclick = () => {
  if (!currentBrowseData) return;
  const query = currentBrowseFilter.toLowerCase().trim();
  const fileItems = currentBrowseData.items.filter(item => !item.directory);
  const matched = query
    ? fileItems.filter(item => item.name.toLowerCase().includes(query) || (item.extension && item.extension.toLowerCase().includes(query)))
    : fileItems;
  for (const f of matched) {
    selected.add(f.path);
    browserKnownFiles.set(f.path, f);
  }
  renderBrowserFiles();
  updateBrowserSelectionSummary();
};

$('select-all-raw').onclick = () => {
  if (!currentBrowseData) return;
  const query = currentBrowseFilter.toLowerCase().trim();
  const fileItems = currentBrowseData.items.filter(item => !item.directory && item.is_raw);
  const matched = query
    ? fileItems.filter(item => item.name.toLowerCase().includes(query) || (item.extension && item.extension.toLowerCase().includes(query)))
    : fileItems;
  for (const f of matched) {
    selected.add(f.path);
    browserKnownFiles.set(f.path, f);
  }
  renderBrowserFiles();
  updateBrowserSelectionSummary();
};

$('select-none').onclick = () => {
  if (!currentBrowseData) {
    selected.clear();
  } else {
    for (const item of currentBrowseData.items) {
      if (!item.directory) selected.delete(item.path);
    }
  }
  renderBrowserFiles();
  updateBrowserSelectionSummary();
};

$('import').onclick = () => {
  $('browser').showModal();
  browse();
};

$('close-browser').onclick = () => $('browser').close();
$('cancel-browser').onclick = () => $('browser').close();

$('confirm-import').onclick = () => {
  if (!selected.size) return;
  const pathsToImport = [...selected];
  $('browser').close();
  task(async () => {
    report(`Importing ${pathsToImport.length} photo${pathsToImport.length > 1 ? 's' : ''}...`);
    const j = await api('/import', { paths: pathsToImport });
    const imported = await waitJob(j.job_id);
    selected.clear();
    updateBrowserSelectionSummary();
    await refresh();
    if (imported && imported.length > 0) {
      const first = imported[0];
      const found = images.find(im => im.id === first.id);
      if (found) select(found);
    }
  });
};



// --- Engine Status & Initialization ---
async function connection() {
  try {
    const s = await api('/status');
    $('connection').textContent = (s.darktable ? 'darktable available' : 'darktable not found') +
      ' · ' + (s.model.available ? 'Local model connected' : 'Local model disconnected');
    $('model').disabled = !s.model.available;
    if (!s.model.available) $('model').checked = false;
  } catch (e) {
    report(e.message);
  }
}

function initPromptChips() {
  const chips = document.querySelectorAll('.prompt-chip');
  chips.forEach(chip => {
    chip.onclick = () => {
      const prompt = chip.getAttribute('data-prompt');
      $('intent').value = prompt;
      chips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      $('model').checked = true;
      if (selectedProfiles.size === 0 || (selectedProfiles.size === 1 && !selectedProfiles.has('00_PROMPT_IA'))) {
        selectedProfiles.clear();
        selectedProfiles.add('00_PROMPT_IA');
        for (const c of $('profile-options').querySelectorAll('input[type=checkbox]')) {
          c.checked = (c.value === '00_PROMPT_IA');
        }
        updateProfileSelectionUI();
      }
      $('intent').focus();
    };
  });
  $('intent').addEventListener('input', () => {
    chips.forEach(c => c.classList.remove('active'));
  });
}

async function init() {
  profiles = await api('/profiles');
  initProfileSelector();
  initPromptChips();

  for (const r of await api('/renders')) {
    const items = results.get(r.recipe.image_id) || [];
    items.push(r);
    results.set(r.recipe.image_id, items);
  }

  await refresh();
  await connection();
  setInterval(connection, 15000);
}

init().catch(e => report(e.message));
