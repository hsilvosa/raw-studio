const $ = id => document.getElementById(id);

let images = [], profiles = [], current = null, chosen = null, busy = false;
let lastExportedPath = '';
const results = new Map(), selected = new Set();
const selectedProfiles = new Set();
const selectedRenders = new Set();


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
  if (!text || (!text.startsWith('Saved to ') && !text.startsWith('Exported ') && !text.startsWith('Opened in file explorer: '))) {
    hideExportActions();
  }
}

function showExportActions(path) {
  lastExportedPath = path || '';
  const actions = $('status-actions');
  if (actions) {
    actions.style.display = path ? 'inline-flex' : 'none';
  }
}

function hideExportActions() {
  const actions = $('status-actions');
  if (actions) {
    actions.style.display = 'none';
  }
}

function buttons() {
  const hasProfile = selectedProfiles.size > 0;
  $('develop').disabled = busy || !current || !hasProfile;
  $('all').disabled = busy || !current || profiles.length === 0;
  const currentResults = current ? (results.get(current.id) || []) : [];
  const exportBtn = $('export');
  const openModalBtn = $('open-export-modal');
  if (exportBtn) {
    if (selectedRenders.size > 0) {
      exportBtn.disabled = busy;
      exportBtn.textContent = `Export (${selectedRenders.size})`;
    } else {
      exportBtn.disabled = busy || !chosen;
      exportBtn.textContent = 'Export';
    }
  }
  if (openModalBtn) {
    openModalBtn.disabled = busy || currentResults.length === 0;
  }
  $('import').disabled = busy;
  $('clear-cache').disabled = busy;
  if (selectedProfiles.size === 1 && [...selectedProfiles][0] === '00_PROMPT_IA') {
    $('develop').textContent = 'Develop AI prompt style';
  } else {
    $('develop').textContent = selectedProfiles.size > 1 ? `Develop ${selectedProfiles.size} profiles` : 'Develop profile';
  }
  const applyZonesBtn = $('apply-zones');
  const resetZonesBtn = $('reset-zones');
  const autoBalanceBtn = $('auto-balance-zones');
  if (applyZonesBtn) {
    applyZonesBtn.disabled = busy || !current;
    applyZonesBtn.title = !current ? 'Select a photo first' : 'Apply zonal corrections to photo';
  }
  if (resetZonesBtn) {
    resetZonesBtn.disabled = busy || !current || !zonalActive;
    resetZonesBtn.title = !current ? 'Select a photo first' : (!zonalActive ? 'No active zonal adjustments to reset' : 'Reset all zonal adjustments');
  }
  if (autoBalanceBtn) {
    autoBalanceBtn.disabled = busy || !current;
    autoBalanceBtn.title = !current ? 'Select a photo first' : 'AI automatically calculates optimal adjustments per zone';
  }
  const kirkifyBtn = $('kirkify-btn');
  const kirkifyQuickBtn = $('kirkify-quick-btn');
  const hasPhoto = Boolean(chosen || current);
  if (kirkifyBtn) {
    kirkifyBtn.disabled = busy || !hasPhoto;
    kirkifyBtn.title = !hasPhoto ? 'Select or develop a photo first' : 'Detect faces and overlay Charlie Kirk';
  }
  if (kirkifyQuickBtn) {
    kirkifyQuickBtn.disabled = busy || !hasPhoto;
    kirkifyQuickBtn.title = !hasPhoto ? 'Select or develop a photo first' : 'Detect faces and overlay Charlie Kirk';
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
  for (const imgId of ['before', 'after', 'mask-overlay', 'mask-canvas']) {
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

let spacePressed = false;
window.addEventListener('keydown', e => {
  if (e.code === 'Space' && !['input', 'textarea'].includes((document.activeElement?.tagName || '').toLowerCase())) {
    spacePressed = true;
    const canvas = $('mask-canvas');
    if (canvas) {
      canvas.classList.remove('brush-active');
      canvas.classList.add('cursor-grab');
    }
    const brushCursor = $('brush-cursor');
    if (brushCursor) brushCursor.hidden = true;
    vpBefore.classList.add('can-pan');
    vpAfter.classList.add('can-pan');
  }
});
window.addEventListener('keyup', e => {
  if (e.code === 'Space') {
    spacePressed = false;
    const canvas = $('mask-canvas');
    if (canvas) {
      canvas.classList.remove('cursor-grab');
      if (activeZone === 'brush') canvas.classList.add('brush-active');
    }
    if (zoomMode === 'fit') {
      vpBefore.classList.remove('can-pan');
      vpAfter.classList.remove('can-pan');
    }
  }
});

// Attach zoom and pan event listeners to viewports
for (const vp of viewports) {
  vp.addEventListener('wheel', e => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.18 : (1 / 1.18);
    zoomAt(factor, e.clientX, e.clientY, vp);
  }, { passive: false });

  vp.addEventListener('mousedown', e => {
    if (e.button === 1) {
      // Middle-click pan always allowed anywhere
      isDragging = true;
      lastDragX = e.clientX;
      lastDragY = e.clientY;
      vpBefore.classList.add('panning');
      vpAfter.classList.add('panning');
      e.preventDefault();
      return;
    }
    if (e.button !== 0) return;

    // Spacebar held: pan always allowed
    if (spacePressed) {
      isDragging = true;
      lastDragX = e.clientX;
      lastDragY = e.clientY;
      vpBefore.classList.add('panning');
      vpAfter.classList.add('panning');
      e.preventDefault();
      return;
    }

    // In viewport-after, when using manual tools (brush, radial, linear), NEVER pan on left click!
    const isManualTool = ['brush', 'radial', 'linear'].includes(activeZone);
    if (vp === vpAfter && isManualTool) {
      return;
    }

    // In 'fit' mode, normal left-click dragging does NOT pan (prevents accidental sliding of fitted photo)
    if (zoomMode === 'fit') {
      return;
    }

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

// --- Library Selection, Filters & Organization ---
let librarySelecting = false;
const selectedLibraryImages = new Set();
let libSearchQuery = '';
let selectedFolderFilter = '';
let selectedTagFilter = '';
let favoriteFilterOnly = false;
let libraryMetadata = { tags: [], folders: [], favorites_count: 0 };
let activeTagModalTarget = null; // null for batch, or single image object
let activeFolderModalTarget = null;

function setLibrarySelecting(enabled) {
  librarySelecting = enabled;
  $('library').classList.toggle('selecting', enabled);
  $('library-selection-bar').hidden = !enabled;
  $('lib-select-mode').textContent = enabled ? 'Cancel' : 'Select';
  $('lib-select-mode').classList.toggle('active', enabled);
  if (!enabled) selectedLibraryImages.clear();
  updateLibrarySelectionUI();
  renderLibraryItems();
}

function updateLibrarySelectionUI() {
  const count = selectedLibraryImages.size;
  $('lib-delete-btn').textContent = `Delete (${count})`;
  $('lib-delete-btn').disabled = count === 0 || busy;
  $('lib-batch-tag').disabled = count === 0 || busy;
  $('lib-batch-folder').disabled = count === 0 || busy;
  if ($('lib-batch-export')) {
    let developedCount = 0;
    for (const id of selectedLibraryImages) {
      developedCount += (results.get(id) || []).length;
    }
    $('lib-batch-export').disabled = count === 0 || busy || developedCount === 0;
    $('lib-batch-export').title = developedCount > 0 ? `Export ${developedCount} developed photos` : 'No developed photos in selection';
  }
}

async function fetchLibraryMetadata() {
  try {
    libraryMetadata = await api('/library/metadata');
    $('fav-count').textContent = libraryMetadata.favorites_count || 0;

    // Update folder dropdown
    const folderSelect = $('lib-folder-select');
    const prevVal = selectedFolderFilter;
    folderSelect.replaceChildren();
    const optAll = document.createElement('option');
    optAll.value = '';
    optAll.textContent = `All folders (${images.length})`;
    folderSelect.append(optAll);
    for (const f of libraryMetadata.folders || []) {
      const opt = document.createElement('option');
      opt.value = f.folder;
      opt.textContent = `${f.folder} (${f.count})`;
      folderSelect.append(opt);
    }
    folderSelect.value = prevVal;

    // Update tag chips in library bar
    const tagChipsWrap = $('lib-tag-chips');
    tagChipsWrap.replaceChildren();
    const hasTags = libraryMetadata.tags && libraryMetadata.tags.length > 0;
    $('library-chips-bar').hidden = !hasTags;
    if (hasTags) {
      for (const t of libraryMetadata.tags) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'lib-chip' + (selectedTagFilter === t.tag ? ' active' : '');
        chip.textContent = `#${t.tag} (${t.count})`;
        chip.title = `Filter by #${t.tag}`;
        chip.onclick = () => {
          if (selectedTagFilter === t.tag) {
            selectedTagFilter = '';
          } else {
            selectedTagFilter = t.tag;
            favoriteFilterOnly = false;
          }
          updateFilterChipsUI();
          renderLibraryItems();
        };
        tagChipsWrap.append(chip);
      }
    }
  } catch (e) {
    console.error('Failed to load library metadata', e);
  }
}

function updateFilterChipsUI() {
  const favBtn = $('lib-fav-filter-btn');
  if (favBtn) favBtn.classList.toggle('active', favoriteFilterOnly);
  const allChip = $('library-chips-bar')?.querySelector('[data-filter="all"]');
  const favChip = $('library-chips-bar')?.querySelector('[data-filter="favorites"]');
  if (allChip) allChip.classList.toggle('active', !favoriteFilterOnly && !selectedTagFilter);
  if (favChip) favChip.classList.toggle('active', favoriteFilterOnly);
  const tagChips = $('lib-tag-chips')?.querySelectorAll('.lib-chip');
  tagChips?.forEach(c => {
    c.classList.toggle('active', selectedTagFilter && c.textContent.startsWith(`#${selectedTagFilter} `));
  });
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

// Modal dialogs for tags and folders
function openTagModal(targetImage = null) {
  activeTagModalTarget = targetImage;
  const isBatch = targetImage === null;
  const count = isBatch ? selectedLibraryImages.size : 1;
  $('tag-dialog-title').textContent = isBatch ? `Tag ${count} Photos` : `Tags for ${targetImage.name}`;
  $('tag-dialog-desc').textContent = isBatch
    ? `Add tags to ${count} selected photos (comma separated):`
    : 'Enter tags separated by commas:';
  $('tag-dialog-input').value = isBatch ? '' : (targetImage.tags || []).join(', ');

  const suggestions = $('quick-tags-suggestions');
  suggestions.replaceChildren();
  for (const t of libraryMetadata.tags || []) {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'suggestion-pill';
    pill.textContent = '#' + t.tag;
    pill.onclick = () => {
      const cur = $('tag-dialog-input').value.split(',').map(s => s.trim()).filter(Boolean);
      if (!cur.includes(t.tag)) {
        cur.push(t.tag);
        $('tag-dialog-input').value = cur.join(', ');
      }
    };
    suggestions.append(pill);
  }

  $('tag-dialog').showModal();
  $('tag-dialog-input').focus();
}

function openFolderModal(targetImage = null) {
  activeFolderModalTarget = targetImage;
  const isBatch = targetImage === null;
  const count = isBatch ? selectedLibraryImages.size : 1;
  $('folder-dialog-input').value = isBatch ? '' : (targetImage.folder || '');

  const suggestions = $('quick-folders-suggestions');
  suggestions.replaceChildren();
  for (const f of libraryMetadata.folders || []) {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'suggestion-pill';
    pill.textContent = '📁 ' + f.folder;
    pill.onclick = () => {
      $('folder-dialog-input').value = f.folder;
    };
    suggestions.append(pill);
  }

  $('folder-dialog').showModal();
  $('folder-dialog-input').focus();
}

$('close-tag-dialog').onclick = () => $('tag-dialog').close();
$('cancel-tag-dialog').onclick = () => $('tag-dialog').close();
$('save-tag-dialog').onclick = async () => {
  const input = $('tag-dialog-input').value;
  const tagList = input.split(',').map(t => t.trim().replace(/^#/, '')).filter(Boolean);
  $('tag-dialog').close();

  if (activeTagModalTarget) {
    await api(`/images/${activeTagModalTarget.id}/tags`, { tags: tagList });
    activeTagModalTarget.tags = tagList;
  } else {
    const ids = [...selectedLibraryImages];
    if (ids.length && tagList.length) {
      await api('/images/batch/tags', { image_ids: ids, add_tags: tagList, remove_tags: [] });
    }
  }
  await refresh();
  report('Tags updated.');
};

$('close-folder-dialog').onclick = () => $('folder-dialog').close();
$('cancel-folder-dialog').onclick = () => $('folder-dialog').close();
$('save-folder-dialog').onclick = async () => {
  const folderName = ($('folder-dialog-input').value || '').trim() || 'Main';
  $('folder-dialog').close();

  const ids = activeFolderModalTarget ? [activeFolderModalTarget.id] : [...selectedLibraryImages];
  if (ids.length) {
    await api('/images/batch/folder', { image_ids: ids, folder: folderName });
  }
  await refresh();
  report(`Moved to folder ${folderName}.`);
};

// Selection Bar wiring
$('lib-select-mode').onclick = () => setLibrarySelecting(!librarySelecting);
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
$('lib-batch-tag').onclick = () => {
  if (selectedLibraryImages.size > 0) openTagModal(null);
};
$('lib-batch-folder').onclick = () => {
  if (selectedLibraryImages.size > 0) openFolderModal(null);
};

// Search & Filter wiring
$('lib-search').oninput = () => {
  libSearchQuery = $('lib-search').value;
  $('lib-search-clear').hidden = !libSearchQuery;
  renderLibraryItems();
};
$('lib-search-clear').onclick = () => {
  $('lib-search').value = '';
  libSearchQuery = '';
  $('lib-search-clear').hidden = true;
  renderLibraryItems();
};
$('lib-folder-select').onchange = () => {
  selectedFolderFilter = $('lib-folder-select').value;
  renderLibraryItems();
};

const favFilterBtn = $('lib-fav-filter-btn');
if (favFilterBtn) {
  favFilterBtn.onclick = () => {
    favoriteFilterOnly = !favoriteFilterOnly;
    if (favoriteFilterOnly) selectedTagFilter = '';
    updateFilterChipsUI();
    renderLibraryItems();
  };
}

const chipAll = $('library-chips-bar')?.querySelector('[data-filter="all"]');
if (chipAll) {
  chipAll.onclick = () => {
    favoriteFilterOnly = false;
    selectedTagFilter = '';
    updateFilterChipsUI();
    renderLibraryItems();
  };
}
const chipFav = $('library-chips-bar')?.querySelector('[data-filter="favorites"]');
if (chipFav) {
  chipFav.onclick = () => {
    favoriteFilterOnly = !favoriteFilterOnly;
    selectedTagFilter = '';
    updateFilterChipsUI();
    renderLibraryItems();
  };
}

function renderLibraryItems() {
  $('images').replaceChildren();
  const query = libSearchQuery.toLowerCase().trim();

  const filtered = images.filter(im => {
    if (favoriteFilterOnly && !im.favorite) return false;
    if (selectedFolderFilter && im.folder !== selectedFolderFilter) return false;
    if (selectedTagFilter && (!im.tags || !im.tags.includes(selectedTagFilter))) return false;
    if (query) {
      const matchName = im.name.toLowerCase().includes(query);
      const matchFolder = (im.folder || '').toLowerCase().includes(query);
      const matchTags = (im.tags || []).some(t => t.toLowerCase().includes(query));
      if (!matchName && !matchFolder && !matchTags) return false;
    }
    return true;
  });

  for (const im of filtered) {
    const isLibSel = selectedLibraryImages.has(im.id);
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'photo' + (current?.id === im.id ? ' active' : '') + (isLibSel ? ' lib-selected' : '');

    // Multi-select checkbox or Favorite & Delete buttons
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
      // Favorite Star button
      const favBtn = document.createElement('button');
      favBtn.type = 'button';
      favBtn.className = 'photo-fav-btn' + (im.favorite ? ' is-fav' : '');
      favBtn.innerHTML = im.favorite ? '★' : '☆';
      favBtn.title = im.favorite ? 'Remove from favorites' : 'Mark as favorite';
      favBtn.onclick = async (e) => {
        e.stopPropagation();
        const nextFav = !im.favorite;
        im.favorite = nextFav;
        favBtn.classList.toggle('is-fav', nextFav);
        favBtn.innerHTML = nextFav ? '★' : '☆';
        await api(`/images/${im.id}/favorite`, { favorite: nextFav });
        fetchLibraryMetadata();
        if (favoriteFilterOnly && !nextFav) {
          renderLibraryItems();
        }
      };
      b.append(favBtn);

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

    const metaRow = document.createElement('div');
    metaRow.className = 'photo-meta-row';

    const folderSpan = document.createElement('span');
    folderSpan.className = 'photo-folder';
    folderSpan.textContent = '📁 ' + (im.folder || 'Main');
    folderSpan.title = `Folder: ${im.folder || 'Main'}`;

    const addTagBtn = document.createElement('button');
    addTagBtn.type = 'button';
    addTagBtn.className = 'btn-add-tag-inline';
    addTagBtn.textContent = '+';
    addTagBtn.title = 'Add tags';
    addTagBtn.onclick = (e) => {
      e.stopPropagation();
      openTagModal(im);
    };

    metaRow.append(folderSpan, addTagBtn);
    b.append(img, nameSpan, metaRow);

    if (im.tags && im.tags.length > 0) {
      const tagsDiv = document.createElement('div');
      tagsDiv.className = 'photo-tags';
      for (const t of im.tags) {
        const tagSpan = document.createElement('span');
        tagSpan.className = 'photo-tag';
        tagSpan.textContent = '#' + t;
        tagSpan.title = `Filter by #${t}`;
        tagSpan.onclick = (e) => {
          e.stopPropagation();
          selectedTagFilter = (selectedTagFilter === t ? '' : t);
          favoriteFilterOnly = false;
          updateFilterChipsUI();
          renderLibraryItems();
        };
        tagsDiv.append(tagSpan);
      }
      b.append(tagsDiv);
    }

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
  await fetchLibraryMetadata();
  updateFilterChipsUI();
  renderLibraryItems();
  if (!current && images.length) select(images[0]);
}


async function ensureActiveRender() {
  if (chosen) return chosen;
  if (!current) return null;
  const existing = results.get(current.id) || [];
  if (existing.length > 0) {
    showResult(existing[existing.length - 1]);
    return chosen;
  }
  report('Creating initial develop for photo...');
  const pid = (selectedProfiles.size ? [...selectedProfiles][0] : '') || '00_NEUTRAL';
  await develop(pid);
  return chosen;
}

function select(im) {
  current = im;
  selectedRenders.clear();
  $('filename').textContent = im.name;
  $('empty').hidden = true;
  $('pair').hidden = false;
  $('before').src = '/api/images/' + im.id + '/preview';

  const existing = results.get(im.id) || [];
  if (existing.length > 0) {
    showResult(existing[existing.length - 1]);
  } else {
    chosen = null;
    $('after').src = $('before').src;
    $('after-label').textContent = 'BASE DEVELOP';
    $('reason').textContent = 'Ready to develop profile or apply local zones.';
    $('recipe').textContent = '';
    zonalActive = false;
    if ($('zonal-badge')) {
      $('zonal-badge').textContent = 'Off';
      $('zonal-badge').style.color = '';
    }
    if ($('mask-overlay')) $('mask-overlay').hidden = true;
    redrawCanvas();
    if ($('kirkify-badge')) $('kirkify-badge').hidden = true;
    if ($('kirkify-reset-btn')) $('kirkify-reset-btn').style.display = 'none';
  }

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
    const isActive = (result.render_id && b.dataset.renderId === result.render_id) ||
                     (result.url && b.dataset.url === result.url);
    b.classList.toggle('active', Boolean(isActive));
    if (isActive) {
      b.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }

  // Zonal panel sync
  zonalActive = Boolean(result.recipe?.has_zonal);
  if ($('zonal-badge')) {
    $('zonal-badge').textContent = zonalActive ? 'Active' : 'Off';
    $('zonal-badge').style.color = zonalActive ? '#79d479' : '';
  }
  if (result.recipe?.zones_params) {
    Object.assign(zoneParams, result.recipe.zones_params);
  }
  if (result.recipe?.manual_masks) {
    Object.assign(manualMasks, result.recipe.manual_masks);
  }
  loadCurrentZoneInputs();
  redrawCanvas();
  if ($('zone-show-mask')?.checked) {
    updateMaskOverlay();
  } else if ($('mask-overlay')) {
    $('mask-overlay').hidden = true;
  }

  // Kirkify state sync
  const isKirkified = Boolean(result.recipe?.is_kirkified);
  if ($('kirkify-badge')) {
    $('kirkify-badge').hidden = !isKirkified;
    $('kirkify-badge').textContent = 'Fusionado';
  }
  if ($('kirkify-reset-btn')) {
    $('kirkify-reset-btn').style.display = isKirkified ? 'inline-block' : 'none';
  }
  if (result.recipe?.kirkify_intensity) {
    const intInput = $('kirkify-intensity');
    const intVal = $('kirkify-intensity-val');
    if (intInput) {
      intInput.value = Math.round(result.recipe.kirkify_intensity * 100);
      if (intVal) intVal.textContent = intInput.value + '%';
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
    const isChecked = selectedRenders.has(r.render_id);
    b.dataset.renderId = r.render_id || '';
    b.dataset.url = r.url || '';
    b.className = 'result' + (chosen?.render_id === r.render_id ? ' active' : '') + (isChecked ? ' checked' : '') + (hasModel ? ' has-model' : '');

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'result-check';
    check.checked = isChecked;
    check.title = 'Select this development for export';
    check.onclick = (e) => {
      e.stopPropagation();
      if (check.checked) selectedRenders.add(r.render_id);
      else selectedRenders.delete(r.render_id);
      b.classList.toggle('checked', check.checked);
      buttons();
    };

    const thumbWrap = document.createElement('div');
    thumbWrap.className = 'result-thumb-wrap';
    const img = new Image();
    img.src = r.url;
    img.alt = r.recipe.profile_title;
    img.draggable = false;
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

    b.append(check, thumbWrap, title);
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

// --- Zonal Editing State & Logic ---
let activeZone = 'subject';
let zonalActive = false;
let brushMode = 'paint'; // 'paint' or 'erase'
let brushSize = 35;
let isDrawing = false;
let currentStroke = null;

const zoneParams = {
  subject: { light: 0.0, contrast: 0.0, temp: 0, tint: 0, saturation: 1.0, detail: 20, blur: 0, feather: 12, sensitivity: 0, invert: false },
  sky: { light: -0.25, contrast: 0.10, temp: -10, tint: 0, saturation: 1.15, detail: 0, blur: 0, feather: 18, sensitivity: 0, invert: false },
  skin: { light: 0.15, contrast: -0.05, temp: 5, tint: 0, saturation: 1.0, detail: -10, blur: 0, feather: 14, sensitivity: 0, invert: false },
  background: { light: 0.0, contrast: -0.05, temp: 0, tint: 0, saturation: 0.95, detail: -10, blur: 4, feather: 15, sensitivity: 0, invert: false },
  foreground: { light: 0.0, contrast: 0.05, temp: 0, tint: 0, saturation: 1.0, detail: 15, blur: 0, feather: 12, sensitivity: 0, invert: false },
  brush: { light: 0.35, contrast: 0.0, temp: 0, tint: 0, saturation: 1.0, detail: 20, blur: 0, feather: 10, sensitivity: 0, invert: false },
  radial: { light: 0.25, contrast: 0.05, temp: 0, tint: 0, saturation: 1.0, detail: 10, blur: 0, feather: 20, sensitivity: 0, invert: false },
  linear: { light: -0.30, contrast: 0.10, temp: -8, tint: 0, saturation: 1.10, detail: 0, blur: 0, feather: 25, sensitivity: 0, invert: false },
};

const manualMasks = {
  brush: { strokes: [], feather: 10 },
  radial: { cx: 0.5, cy: 0.5, rx: 0.3, ry: 0.3, angle: 0, feather: 20 },
  linear: { x1: 0.5, y1: 0.2, x2: 0.5, y2: 0.8, feather: 25 },
};

let maskTimer = null;
function debounceUpdateMaskOverlay() {
  clearTimeout(maskTimer);
  maskTimer = setTimeout(updateMaskOverlay, 180);
}

function updateMaskOverlay() {
  if (!chosen) return;
  const overlay = $('mask-overlay');
  if (!overlay) return;
  overlay.hidden = false;
  overlay.src = `/api/renders/${chosen.render_id}/zones/mask?zone=${activeZone}&ruby=true&t=${Date.now()}`;
}

function saveCurrentZoneInputs() {
  if (!zoneParams[activeZone]) return;
  zoneParams[activeZone].light = Number($('zone-light').value);
  zoneParams[activeZone].contrast = Number($('zone-contrast').value);
  zoneParams[activeZone].temp = Number($('zone-temp').value);
  zoneParams[activeZone].tint = Number($('zone-tint').value);
  zoneParams[activeZone].saturation = Number($('zone-sat').value);
  zoneParams[activeZone].detail = Number($('zone-detail').value);
  zoneParams[activeZone].blur = Number($('zone-blur').value);
  zoneParams[activeZone].feather = Number($('zone-feather').value);
  zoneParams[activeZone].sensitivity = Number($('zone-sens').value);
  zoneParams[activeZone].invert = $('zone-invert').checked;
}

function loadCurrentZoneInputs() {
  const p = zoneParams[activeZone] || zoneParams['subject'];
  if ($('zone-light')) {
    $('zone-light').value = p.light ?? 0;
    $('zone-light-val').textContent = Number(p.light ?? 0).toFixed(2) + ' EV';
  }
  if ($('zone-contrast')) {
    $('zone-contrast').value = p.contrast ?? 0;
    $('zone-contrast-val').textContent = Number(p.contrast ?? 0).toFixed(2);
  }
  if ($('zone-temp')) {
    $('zone-temp').value = p.temp ?? 0;
    $('zone-temp-val').textContent = p.temp ?? 0;
  }
  if ($('zone-tint')) {
    $('zone-tint').value = p.tint ?? 0;
    $('zone-tint-val').textContent = p.tint ?? 0;
  }
  if ($('zone-sat')) {
    $('zone-sat').value = p.saturation ?? 1.0;
    $('zone-sat-val').textContent = Number(p.saturation ?? 1.0).toFixed(2) + 'x';
  }
  if ($('zone-detail')) {
    $('zone-detail').value = p.detail ?? 0;
    $('zone-detail-val').textContent = (p.detail ?? 0) + ' %';
  }
  if ($('zone-blur')) {
    $('zone-blur').value = p.blur ?? 0;
    $('zone-blur-val').textContent = (p.blur ?? 0) + ' px';
  }
  if ($('zone-feather')) {
    $('zone-feather').value = p.feather ?? 12;
    $('zone-feather-val').textContent = (p.feather ?? 12) + ' px';
  }
  if ($('zone-sens')) {
    $('zone-sens').value = p.sensitivity ?? 0;
    $('zone-sens-val').textContent = p.sensitivity ?? 0;
  }
  if ($('zone-invert')) {
    $('zone-invert').checked = Boolean(p.invert);
  }
}

function redrawCanvas() {
  const canvas = $('mask-canvas');
  if (!canvas || !naturalWidth || !naturalHeight) return;
  if (canvas.width !== naturalWidth || canvas.height !== naturalHeight) {
    canvas.width = naturalWidth;
    canvas.height = naturalHeight;
  }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const isManual = ['brush', 'radial', 'linear'].includes(activeZone);
  canvas.hidden = !isManual;
  canvas.style.pointerEvents = isManual ? 'auto' : 'none';
  canvas.classList.toggle('brush-active', activeZone === 'brush' && !spacePressed);
  if (!isManual) return;

  if (activeZone === 'brush') {
    const strokes = manualMasks.brush?.strokes || [];
    for (const s of strokes) {
      ctx.beginPath();
      ctx.fillStyle = s.erase ? 'rgba(40,40,40,0.65)' : 'rgba(240,45,55,0.35)';
      ctx.strokeStyle = s.erase ? 'rgba(40,40,40,0.65)' : 'rgba(240,45,55,0.35)';
      ctx.lineWidth = s.radius * 2;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      if (s.path && s.path.length > 0) {
        ctx.moveTo(s.path[0].x * naturalWidth, s.path[0].y * naturalHeight);
        for (let i = 1; i < s.path.length; i++) {
          ctx.lineTo(s.path[i].x * naturalWidth, s.path[i].y * naturalHeight);
        }
        ctx.stroke();
      } else if (s.x !== undefined && s.y !== undefined) {
        ctx.arc(s.x * naturalWidth, s.y * naturalHeight, s.radius, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  } else if (activeZone === 'radial') {
    const r = manualMasks.radial;
    const cx = r.cx * naturalWidth;
    const cy = r.cy * naturalHeight;
    const rx = r.rx * naturalWidth;
    const ry = r.ry * naturalHeight;

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((r.angle || 0) * Math.PI / 180);
    ctx.beginPath();
    ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(211, 183, 137, 0.9)';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(0, 0, 4, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(211, 183, 137, 1)';
    ctx.fill();
    ctx.restore();
  } else if (activeZone === 'linear') {
    const l = manualMasks.linear;
    const x1 = l.x1 * naturalWidth;
    const y1 = l.y1 * naturalHeight;
    const x2 = l.x2 * naturalWidth;
    const y2 = l.y2 * naturalHeight;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.strokeStyle = 'rgba(211, 183, 137, 0.9)';
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(x1, y1, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#d3b789';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(x2, y2, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#79d479';
    ctx.fill();

    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.hypot(dx, dy) || 1;
    const perpX = -dy / len;
    const perpY = dx / len;
    const lineLen = Math.max(100, len * 0.8);

    ctx.beginPath();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.7)';
    ctx.moveTo(mx - perpX * lineLen, my - perpY * lineLen);
    ctx.lineTo(mx + perpX * lineLen, my + perpY * lineLen);
    ctx.stroke();
    ctx.restore();
  }
}

function initZonalControls() {
  const allTabs = ['subject', 'sky', 'skin', 'background', 'foreground', 'brush', 'radial', 'linear'];
  for (const z of allTabs) {
    const btn = $('tab-' + z);
    if (!btn) continue;
    btn.onclick = () => {
      saveCurrentZoneInputs();
      activeZone = z;
      for (const other of allTabs) {
        const otherBtn = $('tab-' + other);
        if (otherBtn) otherBtn.classList.toggle('active', other === z);
      }
      const isManual = ['brush', 'radial', 'linear'].includes(z);
      if ($('manual-tool-bar')) $('manual-tool-bar').hidden = !isManual;
      if ($('brush-options')) $('brush-options').hidden = (z !== 'brush');
      if ($('radial-options')) $('radial-options').hidden = (z !== 'radial');
      if ($('linear-options')) $('linear-options').hidden = (z !== 'linear');

      if (z !== 'brush' && brushCursor) brushCursor.hidden = true;
      loadCurrentZoneInputs();
      redrawCanvas();
      if ($('zone-show-mask')?.checked) updateMaskOverlay();
    };
  }

  if ($('brush-size')) {
    $('brush-size').oninput = () => {
      brushSize = Number($('brush-size').value);
      $('brush-size-val').textContent = brushSize + ' px';
      if (brushCursor && !brushCursor.hidden) {
        const diameter = Math.max(8, brushSize * 2 * zoomScale);
        brushCursor.style.width = diameter + 'px';
        brushCursor.style.height = diameter + 'px';
      }
    };
  }
  if ($('brush-mode-paint')) {
    $('brush-mode-paint').onclick = () => {
      brushMode = 'paint';
      $('brush-mode-paint').classList.add('active');
      $('brush-mode-erase').classList.remove('active');
      if (brushCursor) brushCursor.classList.remove('erase-mode');
    };
  }
  if ($('brush-mode-erase')) {
    $('brush-mode-erase').onclick = () => {
      brushMode = 'erase';
      $('brush-mode-erase').classList.add('active');
      $('brush-mode-paint').classList.remove('active');
      if (brushCursor) brushCursor.classList.add('erase-mode');
    };
  }
  if ($('brush-clear-strokes')) {
    $('brush-clear-strokes').onclick = () => {
      manualMasks.brush.strokes = [];
      redrawCanvas();
      if ($('zone-show-mask')?.checked) updateMaskOverlay();
    };
  }
  if ($('radial-reset-shape')) {
    $('radial-reset-shape').onclick = () => {
      manualMasks.radial = { cx: 0.5, cy: 0.5, rx: 0.3, ry: 0.3, angle: 0, feather: 20 };
      redrawCanvas();
      if ($('zone-show-mask')?.checked) updateMaskOverlay();
    };
  }
  if ($('linear-reset-shape')) {
    $('linear-reset-shape').onclick = () => {
      manualMasks.linear = { x1: 0.5, y1: 0.2, x2: 0.5, y2: 0.8, feather: 25 };
      redrawCanvas();
      if ($('zone-show-mask')?.checked) updateMaskOverlay();
    };
  }

  const canvas = $('mask-canvas');
  const brushCursor = $('brush-cursor');
  let dragStartPos = null;

  function updateBrushCursor(e) {
    if (!brushCursor || activeZone !== 'brush' || spacePressed) {
      if (brushCursor) brushCursor.hidden = true;
      return;
    }
    const vpRect = vpAfter.getBoundingClientRect();
    const x = e.clientX - vpRect.left;
    const y = e.clientY - vpRect.top;
    if (x < 0 || y < 0 || x > vpRect.width || y > vpRect.height) {
      brushCursor.hidden = true;
      return;
    }
    brushCursor.hidden = false;
    const diameter = Math.max(8, brushSize * 2 * zoomScale);
    brushCursor.style.width = diameter + 'px';
    brushCursor.style.height = diameter + 'px';
    brushCursor.style.left = x + 'px';
    brushCursor.style.top = y + 'px';
    brushCursor.classList.toggle('erase-mode', brushMode === 'erase');
  }

  vpAfter.addEventListener('pointerenter', updateBrushCursor);
  vpAfter.addEventListener('pointerleave', () => { if (brushCursor) brushCursor.hidden = true; });
  vpAfter.addEventListener('pointermove', updateBrushCursor);

  function getCanvasCoords(e) {
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / zoomScale;
    const y = (e.clientY - rect.top) / zoomScale;
    const nx = Math.max(0, Math.min(1, x / naturalWidth));
    const ny = Math.max(0, Math.min(1, y / naturalHeight));
    return { x, y, nx, ny };
  }

  if (canvas) {
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());

    canvas.addEventListener('mousedown', (e) => {
      if (e.button === 1) return; // Allow middle-click to bubble
      if (!spacePressed && e.button === 0) {
        e.preventDefault();
        e.stopPropagation();
      }
    });

    canvas.addEventListener('pointerdown', (e) => {
      if (spacePressed || e.button !== 0) return;
      if (!['brush', 'radial', 'linear'].includes(activeZone)) return;
      e.preventDefault();
      e.stopPropagation();
      canvas.setPointerCapture(e.pointerId);
      isDrawing = true;
      const pt = getCanvasCoords(e);
      dragStartPos = pt;

      if (activeZone === 'brush') {
        currentStroke = {
          radius: brushSize,
          erase: brushMode === 'erase',
          path: [{ x: pt.nx, y: pt.ny }]
        };
        manualMasks.brush.strokes.push(currentStroke);
        redrawCanvas();
        updateBrushCursor(e);
      }
    });

    canvas.addEventListener('pointermove', (e) => {
      if (activeZone === 'brush') updateBrushCursor(e);
      if (!isDrawing) return;
      e.preventDefault();
      e.stopPropagation();
      const pt = getCanvasCoords(e);

      if (activeZone === 'brush' && currentStroke) {
        currentStroke.path.push({ x: pt.nx, y: pt.ny });
        redrawCanvas();
      } else if (activeZone === 'radial' && dragStartPos) {
        const rx = Math.max(0.04, Math.abs(pt.nx - dragStartPos.nx));
        const ry = Math.max(0.04, Math.abs(pt.ny - dragStartPos.ny));
        manualMasks.radial = {
          cx: dragStartPos.nx,
          cy: dragStartPos.ny,
          rx,
          ry,
          angle: 0,
          feather: zoneParams.radial?.feather ?? 20
        };
        redrawCanvas();
      } else if (activeZone === 'linear' && dragStartPos) {
        manualMasks.linear = {
          x1: dragStartPos.nx,
          y1: dragStartPos.ny,
          x2: pt.nx,
          y2: pt.ny,
          feather: zoneParams.linear?.feather ?? 25
        };
        redrawCanvas();
      }
    });

    const finishDrawing = (e) => {
      if (!isDrawing) return;
      isDrawing = false;
      currentStroke = null;
      dragStartPos = null;
      redrawCanvas();
      if (e) updateBrushCursor(e);
      if ($('zone-show-mask')?.checked) debounceUpdateMaskOverlay();
    };
    canvas.addEventListener('pointerup', finishDrawing);
    canvas.addEventListener('pointercancel', finishDrawing);
  }

  $('zone-light').oninput = () => {
    $('zone-light-val').textContent = Number($('zone-light').value).toFixed(2) + ' EV';
    zoneParams[activeZone].light = Number($('zone-light').value);
  };
  $('zone-contrast').oninput = () => {
    $('zone-contrast-val').textContent = Number($('zone-contrast').value).toFixed(2);
    zoneParams[activeZone].contrast = Number($('zone-contrast').value);
  };
  $('zone-temp').oninput = () => {
    $('zone-temp-val').textContent = $('zone-temp').value;
    zoneParams[activeZone].temp = Number($('zone-temp').value);
  };
  $('zone-tint').oninput = () => {
    $('zone-tint-val').textContent = $('zone-tint').value;
    zoneParams[activeZone].tint = Number($('zone-tint').value);
  };
  $('zone-sat').oninput = () => {
    $('zone-sat-val').textContent = Number($('zone-sat').value).toFixed(2) + 'x';
    zoneParams[activeZone].saturation = Number($('zone-sat').value);
  };
  $('zone-detail').oninput = () => {
    $('zone-detail-val').textContent = $('zone-detail').value + ' %';
    zoneParams[activeZone].detail = Number($('zone-detail').value);
  };
  $('zone-blur').oninput = () => {
    $('zone-blur-val').textContent = $('zone-blur').value + ' px';
    zoneParams[activeZone].blur = Number($('zone-blur').value);
  };
  $('zone-feather').oninput = () => {
    $('zone-feather-val').textContent = $('zone-feather').value + ' px';
    zoneParams[activeZone].feather = Number($('zone-feather').value);
    if (['brush', 'radial', 'linear'].includes(activeZone) && manualMasks[activeZone]) {
      manualMasks[activeZone].feather = Number($('zone-feather').value);
    }
    if ($('zone-show-mask').checked) debounceUpdateMaskOverlay();
  };
  $('zone-sens').oninput = () => {
    $('zone-sens-val').textContent = $('zone-sens').value;
    zoneParams[activeZone].sensitivity = Number($('zone-sens').value);
    if ($('zone-show-mask').checked) debounceUpdateMaskOverlay();
  };
  $('zone-invert').onchange = () => {
    zoneParams[activeZone].invert = $('zone-invert').checked;
    if ($('zone-show-mask').checked) updateMaskOverlay();
  };
  $('zone-show-mask').onchange = () => {
    if ($('zone-show-mask').checked) {
      if (!chosen && current) {
        task(async () => {
          await ensureActiveRender();
          updateMaskOverlay();
        });
      } else {
        updateMaskOverlay();
      }
    } else if ($('mask-overlay')) {
      $('mask-overlay').hidden = true;
    }
  };

  $('auto-balance-zones').onclick = () => task(async () => {
    const activeRender = await ensureActiveRender();
    if (!activeRender) return;
    report('AI analyzing zone balance (subject, sky, skin, background)...');
    const res = await api('/renders/' + activeRender.render_id + '/zones/auto-balance', {});
    if (res.zones_params) {
      Object.assign(zoneParams, res.zones_params);
      loadCurrentZoneInputs();
      report(res.message || 'AI balanced adjustments computed.');
      if ($('zone-show-mask')?.checked) updateMaskOverlay();
    }
  });

  $('apply-zones').onclick = () => task(async () => {
    const activeRender = await ensureActiveRender();
    if (!activeRender) return;
    saveCurrentZoneInputs();
    report('Applying local zonal corrections...');
    const res = await api('/renders/' + activeRender.render_id + '/zones/apply', {
      zones: zoneParams,
      manual_masks: manualMasks
    });
    zonalActive = true;
    $('zonal-badge').textContent = 'Active';
    $('zonal-badge').style.color = '#79d479';
    $('after').src = res.url;
    buttons();
    report('Zonal corrections applied.');
  });

  $('reset-zones').onclick = () => task(async () => {
    if (!chosen) return;
    report('Resetting zones to base develop...');
    const res = await api('/renders/' + chosen.render_id + '/zones/reset', {});
    zonalActive = false;
    $('zonal-badge').textContent = 'Off';
    $('zonal-badge').style.color = '';
    manualMasks.brush.strokes = [];
    redrawCanvas();
    $('after').src = res.url;
    if ($('mask-overlay')) $('mask-overlay').hidden = true;
    $('zone-show-mask').checked = false;
    buttons();
    report('Zonal corrections reset.');
  });
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
  let renderIdsToExport = [];
  if (selectedRenders.size > 0) {
    renderIdsToExport = [...selectedRenders];
  } else if (chosen) {
    renderIdsToExport = [chosen.render_id];
  }
  if (!renderIdsToExport.length) return;

  if (renderIdsToExport.length === 1) {
    report('Exporting full-resolution PNG...');
    const j = await api('/renders/' + renderIdsToExport[0] + '/export', {});
    const r = await waitJob(j.job_id);
    lastExportedPath = r.path || '';
    report('Saved to ' + r.path);
  } else {
    report(`Exporting ${renderIdsToExport.length} selected images at 6000 pixels...`);
    const j = await api('/renders/batch-export', { render_ids: renderIdsToExport });
    const r = await waitJob(j.job_id);
    lastExportedPath = r.path || r.folder || '';
    report(`Exported ${r.count || renderIdsToExport.length} photos to ${r.folder || 'export folder'}`);
  }
  if (lastExportedPath) {
    showExportActions(lastExportedPath);
  }
});


function openExportModal() {
  const currentResults = current ? (results.get(current.id) || []) : [];
  if (!currentResults.length) return;

  $('export-dialog-title').textContent = `Select Developments to Export — ${current.name}`;
  const list = $('export-dialog-list');
  list.replaceChildren();

  // If nothing selected, select chosen or all by default
  if (selectedRenders.size === 0 && chosen) {
    selectedRenders.add(chosen.render_id);
  }

  function updateModalUI() {
    $('export-dialog-count').textContent = `${selectedRenders.size} of ${currentResults.length} selected`;
    $('confirm-export-dialog').disabled = selectedRenders.size === 0 || busy;
    $('confirm-export-dialog').textContent = selectedRenders.size > 1 ? `Export (${selectedRenders.size})` : 'Export';
  }

  for (const r of currentResults) {
    const item = document.createElement('div');
    const isChecked = selectedRenders.has(r.render_id);
    item.className = 'export-dialog-item' + (isChecked ? ' selected' : '');

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = isChecked;

    const thumb = document.createElement('img');
    thumb.className = 'export-dialog-thumb';
    thumb.src = r.url;
    thumb.alt = r.recipe.profile_title;

    const info = document.createElement('div');
    info.className = 'export-dialog-info';
    const title = document.createElement('span');
    title.className = 'export-dialog-title';
    title.textContent = r.recipe.profile_title;
    const sub = document.createElement('span');
    sub.className = 'export-dialog-sub';
    const hasModel = Boolean(r.recipe?.model || r.recipe?.proposal);
    sub.textContent = `${current.name} · ${r.recipe.profile_id}` + (hasModel ? ' [IA]' : '');
    info.append(title, sub);

    item.append(check, thumb, info);

    const toggle = (nextState) => {
      check.checked = nextState;
      if (nextState) selectedRenders.add(r.render_id);
      else selectedRenders.delete(r.render_id);
      item.classList.toggle('selected', nextState);
      updateModalUI();
      drawResults();
      buttons();
    };

    check.onclick = (e) => {
      e.stopPropagation();
      toggle(check.checked);
    };
    item.onclick = () => {
      toggle(!selectedRenders.has(r.render_id));
    };

    list.append(item);
  }

  $('export-dialog-select-all').onclick = () => {
    for (const r of currentResults) selectedRenders.add(r.render_id);
    for (const el of list.children) {
      el.classList.add('selected');
      const c = el.querySelector('input[type="checkbox"]');
      if (c) c.checked = true;
    }
    updateModalUI();
    drawResults();
    buttons();
  };

  $('export-dialog-select-none').onclick = () => {
    selectedRenders.clear();
    for (const el of list.children) {
      el.classList.remove('selected');
      const c = el.querySelector('input[type="checkbox"]');
      if (c) c.checked = false;
    }
    updateModalUI();
    drawResults();
    buttons();
  };

  updateModalUI();
  $('export-modal').showModal();
}

if ($('open-export-modal')) {
  $('open-export-modal').onclick = openExportModal;
}
if ($('close-export-dialog')) {
  $('close-export-dialog').onclick = () => $('export-modal').close();
}
if ($('cancel-export-dialog')) {
  $('cancel-export-dialog').onclick = () => $('export-modal').close();
}
if ($('confirm-export-dialog')) {
  $('confirm-export-dialog').onclick = () => {
    $('export-modal').close();
    $('export').click();
  };
}

if ($('status-open-folder')) {
  $('status-open-folder').onclick = async () => {
    if (!lastExportedPath) return;
    try {
      const res = await api('/system/open-folder', { path: lastExportedPath });
      const shownPath = res.folder || res.opened || lastExportedPath;
      report('Opened in file explorer: ' + shownPath);
      showExportActions(lastExportedPath);
    } catch (e) {
      report(e.message);
    }
  };
}

if ($('status-copy-path')) {
  $('status-copy-path').onclick = async () => {
    if (lastExportedPath && navigator.clipboard) {
      try {
        await navigator.clipboard.writeText(lastExportedPath);
        const btn = $('status-copy-path');
        btn.textContent = 'Copied';
        setTimeout(() => { if ($('status-copy-path')) $('status-copy-path').textContent = 'Copy'; }, 2000);
      } catch (_) {}
    }
  };
}

if ($('lib-batch-export')) {
  $('lib-batch-export').onclick = () => task(async () => {
    const renderIds = [];
    for (const id of selectedLibraryImages) {
      const imgResults = results.get(id) || [];
      for (const r of imgResults) {
        renderIds.push(r.render_id);
      }
    }
    if (!renderIds.length) {
      report('No developed photos found in selected images.');
      return;
    }
    report(`Exporting ${renderIds.length} developed photos at 6000 pixels...`);
    const j = await api('/renders/batch-export', { render_ids: renderIds });
    const r = await waitJob(j.job_id);
    lastExportedPath = r.path || r.folder || '';
    report(`Exported ${r.count || renderIds.length} photos to ${r.folder || 'export folder'}`);
    if (lastExportedPath) {
      showExportActions(lastExportedPath);
    }
  });
}

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

function initKirkifyControls() {
  const kirkifyBtn = $('kirkify-btn');
  const kirkifyQuickBtn = $('kirkify-quick-btn');
  const kirkifyResetBtn = $('kirkify-reset-btn');
  const intensityInput = $('kirkify-intensity');
  const intensityVal = $('kirkify-intensity-val');

  if (intensityInput && intensityVal) {
    intensityInput.oninput = () => {
      intensityVal.textContent = intensityInput.value + '%';
    };
  }

  const onKirkify = async () => {
    if (busy || (!chosen && !current)) return;
    await task(async () => {
      const intensity = parseFloat($('kirkify-intensity')?.value || '75') / 100.0;
      report('🎭 Detectando rostros y aplicando Fusión Facial...');
      try {
        const payload = chosen ? { render_id: chosen.render_id } : { image_id: current.id };
        payload.mode = 'fusion';
        payload.intensity = intensity;

        const res = await api('/kirkify', payload);
        if (chosen) {
          chosen.url = res.url;
          if (chosen.recipe) {
            chosen.recipe.is_kirkified = true;
            chosen.recipe.kirkify_mode = 'fusion';
            chosen.recipe.kirkify_intensity = intensity;
            chosen.recipe.kirkify_faces = res.faces_found;
          }
          $('after').src = res.url;
          if (chosen.render_id) {
            for (const b of $('results').children) {
              if (b.dataset.renderId === chosen.render_id) {
                const img = b.querySelector('img');
                if (img) img.src = res.url;
              }
            }
          }
        } else if (current) {
          $('before').src = res.url;
          $('after').src = res.url;
        }
        if ($('kirkify-badge')) {
          $('kirkify-badge').hidden = false;
          $('kirkify-badge').textContent = 'Fusionado';
        }
        if ($('kirkify-reset-btn')) $('kirkify-reset-btn').style.display = 'inline-block';
        report(res.message);
      } catch (err) {
        report('Error al fusionar rostros: ' + err.message);
      }
    });
  };

  const onResetKirkify = async () => {
    if (busy || !chosen) return;
    await task(async () => {
      report('Revirtiendo Kirkificación...');
      try {
        const res = await api('/renders/' + chosen.render_id + '/kirkify/reset', {});
        chosen.url = res.url;
        if (chosen.recipe) {
          chosen.recipe.is_kirkified = false;
        }
        $('after').src = res.url;
        for (const b of $('results').children) {
          if (b.dataset.renderId === chosen.render_id) {
            const img = b.querySelector('img');
            if (img) img.src = res.url;
          }
        }
        if ($('kirkify-badge')) $('kirkify-badge').hidden = true;
        if ($('kirkify-reset-btn')) $('kirkify-reset-btn').style.display = 'none';
        report(res.message);
      } catch (err) {
        report('Error al revertir: ' + err.message);
      }
    });
  };

  if (kirkifyBtn) kirkifyBtn.onclick = onKirkify;
  if (kirkifyQuickBtn) kirkifyQuickBtn.onclick = onKirkify;
  if (kirkifyResetBtn) kirkifyResetBtn.onclick = onResetKirkify;
}

async function init() {
  profiles = await api('/profiles');
  initProfileSelector();
  initPromptChips();
  initZonalControls();
  initKirkifyControls();

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
