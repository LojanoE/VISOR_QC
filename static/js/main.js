// ─── API Helper ───
function apiUrl(path) {
    return (typeof API_BASE !== 'undefined' && API_BASE ? API_BASE : '') + path;
}

function apiFetch(url, options = {}) {
    if (typeof API_BASE !== 'undefined' && API_BASE) {
        options.credentials = 'include';
    }
    return fetch(apiUrl(url), options);
}

// ─── State ───
let mapObj = null;
let mapImageOverlay = null;
let markersLayer = null;
let allPhotos = [];
let filteredPhotos = [];
let visiblePhotos = [];
let currentPhotoIndex = -1;
let appConfig = {};
let mapBounds = null;
let dateRange = { min_date: null, max_date: null };

const FILTER_FIELDS = ['work_front', 'coronation', 'activity_performed', 'observation_category'];
const FILTER_TITLES = {
    work_front: 'Frente de Trabajo',
    coronation: 'Coronamiento',
    activity_performed: 'Actividad Realizada',
    observation_category: 'Categoría de Observación'
};

let filterSelections = {
    work_front: new Set(),
    coronation: new Set(),
    activity_performed: new Set(),
    observation_category: new Set()
};

let noneSelectedState = {
    work_front: false,
    coronation: false,
    activity_performed: false,
    observation_category: false
};

let onlyTextState = {
    work_front: false,
    coronation: false,
    activity_performed: false,
    observation_category: false
};

let searchQueries = {
    work_front: '',
    coronation: '',
    activity_performed: '',
    observation_category: ''
};

let filterOptionsCache = {
    work_front: { values: [], counts: {} },
    coronation: { values: [], counts: {} },
    activity_performed: { values: [], counts: {} },
    observation_category: { values: [], counts: {} }
};

// ─── Auth ───
async function checkSession() {
    try {
        const resp = await apiFetch('/api/session');
        if (resp.ok) {
            const data = await resp.json();
            return data.username || '';
        }
        return '';
    } catch (e) {
        return '';
    }
}

async function doLogout() {
    try {
        await apiFetch('/api/logout');
    } catch (e) {}
    window.location.href = 'login.html';
}

function showConnectionError() {
    const serverInfo = (typeof API_BASE !== 'undefined' && API_BASE) ? `<p>Dirección del servidor: <code>${API_BASE}</code></p>` : '';
    document.body.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#fff;background:#1a1a2e;text-align:center;padding:20px;">
            <div>
                <h2 style="color:#4fc3f7;">No se puede conectar al servidor</h2>
                <p>Verifique que está conectado a la red de la empresa y que el servidor esté en funcionamiento.</p>
                ${serverInfo}
                <button onclick="location.reload()" style="margin-top:16px;padding:10px 24px;background:#4fc3f7;color:#000;border:none;border-radius:4px;cursor:pointer;font-size:14px;">Reintentar</button>
            </div>
        </div>`;
}

// ─── Init ───
document.addEventListener('DOMContentLoaded', async () => {
    const username = await checkSession();
    if (username === '') {
        const apiBase = (typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '';
        if (apiBase) {
            try {
                const resp = await fetch(apiBase + '/api/session', { method: 'GET', credentials: 'include' });
                if (!resp.ok) {
                    showConnectionError();
                    return;
                }
            } catch (e) {
                showConnectionError();
                return;
            }
        } else {
            window.location.href = 'login.html';
            return;
        }
        window.location.href = 'login.html';
        return;
    }
    document.getElementById('current-user').textContent = username;

    initTabs();
    loadConfig().then(() => {
        initMap();
        if (appConfig.map_image_path && appConfig.coords_file_path) {
            loadMapImage();
        }
        if (appConfig.photos_folder_path) {
            initDateRangeAndLoad();
        }
    });
    initFilterWidgets();
});

async function initDateRangeAndLoad() {
    try {
        const resp = await apiFetch('/api/photos/daterange');
        dateRange = await resp.json();
        if (dateRange.max_date) {
            const maxD = new Date(dateRange.max_date + 'T00:00:00');
            const minD = dateRange.min_date ? new Date(dateRange.min_date + 'T00:00:00') : maxD;
            const weekAgo = new Date(maxD);
            weekAgo.setDate(weekAgo.getDate() - 30);
            const startDate = weekAgo < minD ? minD : weekAgo;

            const dateFrom = document.getElementById('date-from');
            const dateTo = document.getElementById('date-to');
            dateFrom.value = startDate.toISOString().split('T')[0];
            dateTo.value = maxD.toISOString().split('T')[0];
            dateFrom.min = dateRange.min_date || '';
            dateTo.min = dateRange.min_date || '';
            dateFrom.max = dateRange.max_date || '';
            dateTo.max = dateRange.max_date || '';

            document.getElementById('use-date-filter').checked = true;
            document.getElementById('date-fields').classList.remove('hidden');
        }
    } catch (e) {
        console.error('Error loading date range:', e);
    }
    await loadPhotos();
}

function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
            if (btn.dataset.tab === 'map' && mapObj) {
                setTimeout(() => mapObj.invalidateSize(), 100);
            }
            if (btn.dataset.tab === 'config') {
                loadUsers();
            }
        });
    });
}

// ─── Config ───
async function loadConfig() {
    try {
        const resp = await apiFetch('/api/config');
        appConfig = await resp.json();
        document.getElementById('cfg-map-image').value = appConfig.map_image_path || '';
        document.getElementById('cfg-coords-file').value = appConfig.coords_file_path || '';
        document.getElementById('cfg-photos-folder').value = appConfig.photos_folder_path || '';
        document.getElementById('cfg-docs-folder').value = appConfig.docs_folder_path || '';
        const display = document.getElementById('photos-folder-display');
        display.textContent = appConfig.photos_folder_path || 'Sin carpeta seleccionada';
        display.title = appConfig.photos_folder_path || '';
    } catch (e) {
        console.error('Error loading config:', e);
    }
}

async function saveConfig() {
    const config = {
        map_image_path: document.getElementById('cfg-map-image').value,
        coords_file_path: document.getElementById('cfg-coords-file').value,
        photos_folder_path: document.getElementById('cfg-photos-folder').value,
        docs_folder_path: document.getElementById('cfg-docs-folder').value
    };
    try {
        await apiFetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        appConfig = config;
        document.getElementById('photos-folder-display').textContent = config.photos_folder_path || 'Sin carpeta seleccionada';
        document.getElementById('photos-folder-display').title = config.photos_folder_path || '';
        alert('Configuración guardada');
        if (config.map_image_path && config.coords_file_path) {
            loadMapImage();
        }
        if (config.photos_folder_path) {
            initDateRangeAndLoad();
        }
    } catch (e) {
        alert('Error al guardar la configuración: ' + e.message);
    }
}

// ─── Browse paths ───
let browseCallback = null;
let browseCurrentPath = '';
let browseType = 'dir';

function browsePath(inputId, type) {
    browseCallback = (path) => {
        document.getElementById(inputId).value = path;
    };
    browseType = type;
    browseCurrentPath = '';
    loadBrowseList('');
    document.getElementById('browse-modal').classList.remove('hidden');
}

async function loadBrowseList(dirPath) {
    browseCurrentPath = dirPath;
    document.getElementById('browse-current-path').value = dirPath || '(Raíz)';
    try {
        const resp = await apiFetch(`/api/config/paths?dir=${encodeURIComponent(dirPath)}`);
        const data = await resp.json();
        const list = document.getElementById('browse-list');
        list.innerHTML = '';
        data.dirs.forEach(d => {
            const item = document.createElement('div');
            item.className = 'browse-item';
            item.textContent = d.split(/[\\/]/).pop() || d;
            item.title = d;
            item.addEventListener('click', () => {
                list.querySelectorAll('.browse-item').forEach(i => i.classList.remove('selected'));
                item.classList.add('selected');
                browseCurrentPath = d;
                document.getElementById('browse-current-path').value = d;
            });
            item.addEventListener('dblclick', () => {
                loadBrowseList(d);
            });
            list.appendChild(item);
        });
    } catch (e) {
        console.error('Error browsing paths:', e);
    }
}

function browseGoUp() {
    apiFetch(`/api/config/paths?dir=${encodeURIComponent(browseCurrentPath)}`)
        .then(r => r.json())
        .then(data => {
            if (data.parent) loadBrowseList(data.parent);
        });
}

function confirmBrowse() {
    if (browseCallback && browseCurrentPath) {
        browseCallback(browseCurrentPath);
    }
    closeBrowseModal();
}

function closeBrowseModal() {
    document.getElementById('browse-modal').classList.add('hidden');
}

// ─── Map ───
function initMap() {
    mapObj = L.map('map', {
        crs: L.CRS.Simple,
        minZoom: -2,
        maxZoom: 5,
        zoomSnap: 0.25,
        zoomDelta: 0.5
    }).setView([0, 0], 0);
    markersLayer = L.layerGroup().addTo(mapObj);
}

async function loadMapImage() {
    try {
        const boundsResp = await apiFetch('/api/map/bounds');
        const bounds = await boundsResp.json();
        if (bounds.error) {
            console.error('Bounds error:', bounds.error);
            return;
        }
        mapBounds = bounds;
        const corner1 = [bounds.minY, bounds.minX];
        const corner2 = [bounds.maxY, bounds.maxX];
        const imageBounds = [corner1, corner2];
        if (mapImageOverlay) {
            mapObj.removeLayer(mapImageOverlay);
        }
        mapImageOverlay = L.imageOverlay(apiUrl('/api/map/image'), imageBounds).addTo(mapObj);
        mapObj.fitBounds(imageBounds);
    } catch (e) {
        console.error('Error loading map:', e);
    }
}

function resetZoom() {
    if (mapImageOverlay) {
        mapObj.fitBounds(mapImageOverlay.getBounds());
    }
}

// ─── Photos ───
async function loadPhotos() {
    showLoading(true);
    try {
        const params = buildFilterParams();
        const [photosResp] = await Promise.all([
            apiFetch('/api/photos?' + params),
            updateFilterOptions()
        ]);
        allPhotos = await photosResp.json();
        filteredPhotos = allPhotos;
        renderMarkers();
        updatePhotoCount();
    } catch (e) {
        console.error('Error loading photos:', e);
    } finally {
        showLoading(false);
    }
}

function renderMarkers() {
    if (markersLayer) {
        markersLayer.clearLayers();
    }
    if (!mapBounds) return;

    filteredPhotos.forEach(photo => {
        if (photo.utm_x == null || photo.utm_y == null) return;
        const marker = L.circleMarker([photo.utm_y, photo.utm_x], {
            radius: 6,
            fillColor: '#FF6347',
            fillOpacity: 0.9,
            color: '#000',
            weight: 1
        });

        const popupContent = buildPopupContent(photo);
        marker.bindPopup(popupContent, { maxWidth: 300, className: 'photo-tooltip' });

        const tooltipContent = buildTooltipContent(photo);
        marker.bindTooltip(tooltipContent, { direction: 'top', offset: [0, -8] });

        marker.on('click', () => {
            const visible = getVisiblePhotosSorted();
            const idx = visible.findIndex(p => p.path === photo.path);
            currentPhotoIndex = idx >= 0 ? idx : 0;
            visiblePhotos = visible.length > 0 ? visible : [photo];
            openPhotoModal(photo);
        });

        markersLayer.addLayer(marker);
    });
}

function buildPopupContent(photo) {
    const thumbUrl = apiUrl(`/api/photo/thumbnail?path=${encodeURIComponent(photo.path)}`);
    let html = `<img src="${thumbUrl}" class="tooltip-thumb" alt="foto" onerror="this.style.display='none'"/>`;
    if (photo.date) html += `<div class="tooltip-field"><span class="tooltip-label">Fecha:</span> ${photo.date}</div>`;
    if (photo.work_front) html += `<div class="tooltip-field"><span class="tooltip-label">Frente:</span> ${photo.work_front}</div>`;
    if (photo.coronation) html += `<div class="tooltip-field"><span class="tooltip-label">Coronamiento:</span> ${photo.coronation}</div>`;
    if (photo.activity_performed) html += `<div class="tooltip-field"><span class="tooltip-label">Actividad:</span> ${photo.activity_performed}</div>`;
    if (photo.observation_category) html += `<div class="tooltip-field"><span class="tooltip-label">Categoría:</span> ${photo.observation_category}</div>`;
    return html;
}

function buildTooltipContent(photo) {
    const name = photo.path.split(/[\\/]/).pop();
    let text = name;
    if (photo.work_front) text += ` | ${photo.work_front}`;
    return text;
}

function getVisiblePhotosSorted() {
    visiblePhotos = [...filteredPhotos].sort((a, b) => {
        const da = a.date || '';
        const db = b.date || '';
        return da < db ? -1 : da > db ? 1 : 0;
    });
    return visiblePhotos;
}

// ─── Photo Modal ───
function openPhotoModal(photo) {
    const modal = document.getElementById('photo-modal');
    const img = document.getElementById('photo-modal-img');
    const detailsList = document.getElementById('photo-details-list');

    img.src = apiUrl(`/api/photo/image?path=${encodeURIComponent(photo.path)}`);
    img.onclick = () => {
        window.open(apiUrl(`/api/photo/image?path=${encodeURIComponent(photo.path)}`), '_blank');
    };

    detailsList.innerHTML = '';
    const fields = [
        { label: 'Coordenadas', value: photo.lat && photo.lon ? `Lat: ${photo.lat.toFixed(6)}, Lng: ${photo.lon.toFixed(6)}` : null },
        { label: 'Fecha', value: photo.date },
        { label: 'Frente de Trabajo', value: photo.work_front },
        { label: 'Coronamiento', value: photo.coronation },
        { label: 'Actividad Realizada', value: photo.activity_performed },
        { label: 'Categoría de Observación', value: photo.observation_category }
    ];

    fields.forEach(f => {
        if (f.value) {
            const div = document.createElement('div');
            div.className = 'photo-detail-item';
            div.innerHTML = `<div class="detail-label">${f.label}</div><div class="detail-value">${f.value}</div>`;
            detailsList.appendChild(div);
        }
    });

    document.getElementById('btn-download').onclick = () => {
        const a = document.createElement('a');
        a.href = apiUrl(`/api/photo/image?path=${encodeURIComponent(photo.path)}`);
        a.download = photo.path.split(/[\\/]/).pop();
        a.click();
    };

    updateNavButtons();
    modal.classList.remove('hidden');
}

function updateNavButtons() {
    const prevBtn = document.getElementById('btn-prev');
    const nextBtn = document.getElementById('btn-next');
    if (visiblePhotos.length === 0) {
        prevBtn.disabled = true;
        nextBtn.disabled = true;
        return;
    }
    prevBtn.disabled = currentPhotoIndex <= 0;
    nextBtn.disabled = currentPhotoIndex >= visiblePhotos.length - 1;
}

function navigatePhoto(delta) {
    const newIdx = currentPhotoIndex + delta;
    if (newIdx >= 0 && newIdx < visiblePhotos.length) {
        currentPhotoIndex = newIdx;
        openPhotoModal(visiblePhotos[newIdx]);
    }
}

function closePhotoModal() {
    document.getElementById('photo-modal').classList.add('hidden');
}

// ─── Metadata Editor ───
function openMetadataEditor() {
    if (visiblePhotos.length === 0 || currentPhotoIndex < 0) return;
    const photo = visiblePhotos[currentPhotoIndex];
    document.getElementById('meta-work-front').value = photo.work_front || '';
    document.getElementById('meta-coronation').value = photo.coronation || '';
    document.getElementById('meta-activity').value = photo.activity_performed || '';
    document.getElementById('meta-category').value = photo.observation_category || '';
    document.getElementById('metadata-modal').classList.remove('hidden');
}

function closeMetadataEditor() {
    document.getElementById('metadata-modal').classList.add('hidden');
}

async function saveMetadata() {
    if (visiblePhotos.length === 0 || currentPhotoIndex < 0) return;
    const photo = visiblePhotos[currentPhotoIndex];
    const data = {
        path: photo.path,
        work_front: document.getElementById('meta-work-front').value,
        coronation: document.getElementById('meta-coronation').value,
        activity_performed: document.getElementById('meta-activity').value,
        observation_category: document.getElementById('meta-category').value
    };

    try {
        const resp = await apiFetch('/api/photo/metadata', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (resp.ok) {
            photo.work_front = data.work_front;
            photo.coronation = data.coronation;
            photo.activity_performed = data.activity_performed;
            photo.observation_category = data.observation_category;
            const idx = filteredPhotos.findIndex(p => p.path === photo.path);
            if (idx >= 0) filteredPhotos[idx] = { ...filteredPhotos[idx], ...data };
            openPhotoModal(photo);
            closeMetadataEditor();
            await applyFilters();
        } else {
            const err = await resp.json();
            alert('Error: ' + (err.error || 'Error desconocido'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

// ─── Dependent Filters ───
function initFilterWidgets() {
    const container = document.getElementById('filter-widgets');
    container.innerHTML = '';

    FILTER_FIELDS.forEach(field => {
        const group = document.createElement('div');
        group.className = 'filter-group';
        group.id = `filter-group-${field}`;

        const title = document.createElement('div');
        title.className = 'filter-group-title';
        title.textContent = FILTER_TITLES[field];
        title.innerHTML += ' <span class="toggle-arrow">&#9662;</span>';
        title.addEventListener('click', () => {
            const body = group.querySelector('.filter-group-body');
            body.classList.toggle('open');
        });

        const body = document.createElement('div');
        body.className = 'filter-group-body';
        body.id = `filter-body-${field}`;

        const search = document.createElement('input');
        search.type = 'text';
        search.className = 'filter-search';
        search.placeholder = `Buscar ${FILTER_TITLES[field].toLowerCase()}...`;
        search.value = searchQueries[field];
        search.addEventListener('input', (e) => {
            searchQueries[field] = e.target.value.toLowerCase();
            updateFilterCheckboxVisibility(field);
        });
        body.appendChild(search);

        const btnRow = document.createElement('div');
        btnRow.className = 'filter-quick-btns';
        const btnAll = document.createElement('button');
        btnAll.textContent = 'Todos';
        btnAll.addEventListener('click', () => toggleAll(field, true));
        const btnNone = document.createElement('button');
        btnNone.textContent = 'Ninguno';
        btnNone.addEventListener('click', () => toggleAll(field, false));
        const btnOnlyText = document.createElement('button');
        btnOnlyText.className = 'only-text-btn';
        btnOnlyText.textContent = 'Solo con Texto';
        if (onlyTextState[field]) btnOnlyText.classList.add('active');
        btnOnlyText.addEventListener('click', () => toggleOnlyText(field));
        btnRow.appendChild(btnAll);
        btnRow.appendChild(btnNone);
        btnRow.appendChild(btnOnlyText);
        body.appendChild(btnRow);

        const checkboxContainer = document.createElement('div');
        checkboxContainer.id = `filter-checkboxes-${field}`;
        body.appendChild(checkboxContainer);

        const countLabel = document.createElement('div');
        countLabel.className = 'filter-count-label';
        countLabel.id = `filter-count-${field}`;
        countLabel.textContent = '';
        body.appendChild(countLabel);

        group.appendChild(title);
        group.appendChild(body);
        container.appendChild(group);
    });
}

async function updateFilterOptions() {
    const params = buildFilterParams();
    try {
        const resp = await apiFetch('/api/filters/options?' + params);
        const options = await resp.json();

        FILTER_FIELDS.forEach(field => {
            const data = options[field] || { values: [], counts: {} };
            filterOptionsCache[field] = data;
            renderFilterCheckboxes(field, data);
        });
    } catch (e) {
        console.error('Error updating filter options:', e);
    }
}

function renderFilterCheckboxes(field, data) {
    const container = document.getElementById(`filter-checkboxes-${field}`);
    if (!container) return;
    const currentValues = data.values || [];
    const counts = data.counts || {};

    container.innerHTML = '';

    let checkedCount = 0;
    const totalCount = currentValues.length;

    currentValues.forEach(val => {
        const label = document.createElement('label');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = val;

        if (noneSelectedState[field]) {
            cb.checked = false;
        } else if (filterSelections[field].size > 0) {
            cb.checked = filterSelections[field].has(val);
        } else {
            cb.checked = true;
        }

        if (cb.checked) checkedCount++;

        const count = counts[val] || 0;
        const text = document.createTextNode(` ${val} (${count})`);
        label.appendChild(cb);
        label.appendChild(text);

        cb.addEventListener('change', () => {
            syncSelectionsFromCheckboxes(field);
            applyFilters();
        });

        label.dataset.value = val.toLowerCase();
        container.appendChild(label);
    });

    updateFilterCheckboxVisibility(field);

    const countLabel = document.getElementById(`filter-count-${field}`);
    if (countLabel) {
        const filteredCount = Object.values(counts).reduce((a, b) => a + b, 0);
        countLabel.textContent = `${checkedCount}/${totalCount} seleccionados · ${filteredCount} fotos`;
    }
}

function updateFilterCheckboxVisibility(field) {
    const container = document.getElementById(`filter-checkboxes-${field}`);
    if (!container) return;

    const search = searchQueries[field];
    const data = filterOptionsCache[field] || {};
    const counts = data.counts || {};

    container.querySelectorAll('label').forEach(label => {
        const val = label.dataset.value || '';
        const originalVal = label.querySelector('input[type="checkbox"]')?.value || '';
        let visible = true;

        if (search && !val.includes(search)) {
            visible = false;
        }

        if (onlyTextState[field] && val.startsWith('sin ')) {
            visible = false;
        }

        const count = counts[originalVal] || 0;
        if (!search && count === 0) {
            visible = false;
        }

        label.style.display = visible ? '' : 'none';
    });
}

function syncSelectionsFromCheckboxes(field) {
    const container = document.getElementById(`filter-checkboxes-${field}`);
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');

    const checked = new Set();
    let allChecked = true;

    checkboxes.forEach(cb => {
        if (cb.checked) {
            checked.add(cb.value);
        } else {
            allChecked = false;
        }
    });

    if (allChecked) {
        filterSelections[field] = new Set();
        noneSelectedState[field] = false;
    } else {
        filterSelections[field] = checked;
        noneSelectedState[field] = false;
    }
}

function toggleAll(field, state) {
    const container = document.getElementById(`filter-checkboxes-${field}`);
    container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.checked = state;
    });

    if (state) {
        filterSelections[field] = new Set();
        noneSelectedState[field] = false;
    } else {
        filterSelections[field] = new Set();
        noneSelectedState[field] = true;
    }

    applyFilters();
}

function toggleOnlyText(field) {
    onlyTextState[field] = !onlyTextState[field];
    const btn = document.querySelector(`#filter-body-${field} .only-text-btn`);
    if (btn) btn.classList.toggle('active', onlyTextState[field]);
    updateFilterCheckboxVisibility(field);
}

async function applyFilters() {
    showLoading(true);
    try {
        const params = buildFilterParams();
        const [photosResp] = await Promise.all([
            apiFetch('/api/photos?' + params),
            updateFilterOptions()
        ]);
        filteredPhotos = await photosResp.json();
        renderMarkers();
        updatePhotoCount();
    } catch (e) {
        console.error('Error applying filters:', e);
    } finally {
        showLoading(false);
    }
}

function buildFilterParams() {
    const params = new URLSearchParams();

    const useDate = document.getElementById('use-date-filter').checked;
    if (useDate) {
        const from = document.getElementById('date-from').value;
        const to = document.getElementById('date-to').value;
        if (from) params.set('date_from', from);
        if (to) params.set('date_to', to);
    }

    const shift = document.getElementById('shift-filter').value;
    if (shift !== 'Ambos') params.set('shift', shift);

    FILTER_FIELDS.forEach(field => {
        if (filterSelections[field].size > 0) {
            filterSelections[field].forEach(val => {
                params.append(field, val);
            });
        }
    });

    return params.toString();
}

function onDateFilterToggle() {
    const checked = document.getElementById('use-date-filter').checked;
    const fields = document.getElementById('date-fields');
    if (checked) {
        fields.classList.remove('hidden');
    } else {
        fields.classList.add('hidden');
    }
    applyFilters();
}

function clearFilters() {
    document.getElementById('use-date-filter').checked = false;
    document.getElementById('date-fields').classList.add('hidden');
    document.getElementById('date-from').value = '';
    document.getElementById('date-to').value = '';
    document.getElementById('shift-filter').value = 'Ambos';

    FILTER_FIELDS.forEach(field => {
        filterSelections[field] = new Set();
        noneSelectedState[field] = false;
        onlyTextState[field] = false;
        searchQueries[field] = '';
        const searchEl = document.querySelector(`#filter-body-${field} .filter-search`);
        if (searchEl) searchEl.value = '';
    });

    applyFilters();
}

function updatePhotoCount() {
    document.getElementById('photo-count').textContent = `Fotos en el mapa: ${filteredPhotos.length}`;
}

// ─── Cache / Scan ───
async function reloadCache() {
    if (!appConfig.photos_folder_path) {
        alert('Seleccione primero una carpeta de fotos en Configuración');
        return;
    }

    const progressDiv = document.getElementById('scan-progress-bar');
    const fillDiv = document.getElementById('scan-progress-fill');
    const msgDiv = document.getElementById('scan-message');
    progressDiv.classList.remove('hidden');
    msgDiv.classList.remove('hidden');
    fillDiv.style.width = '0%';
    msgDiv.textContent = 'Iniciando escaneo...';

    try {
        await apiFetch('/api/cache/scan', { method: 'POST' });

        const checkProgress = async () => {
            try {
                const resp = await apiFetch('/api/cache/status');
                const state = await resp.json();
                fillDiv.style.width = state.progress + '%';
                msgDiv.textContent = state.message || `Escaneando... ${state.current}/${state.total}`;

                if (state.running) {
                    setTimeout(checkProgress, 500);
                } else {
                    msgDiv.textContent = 'Escaneo completado';
                    fillDiv.style.width = '100%';
                    setTimeout(() => {
                        progressDiv.classList.add('hidden');
                        msgDiv.classList.add('hidden');
                    }, 2000);
                    await initDateRangeAndLoad();
                }
            } catch (e) {
                msgDiv.textContent = 'Error verificando progreso';
                setTimeout(checkProgress, 1000);
            }
        };
        setTimeout(checkProgress, 500);
    } catch (e) {
        alert('Error: ' + e.message);
        progressDiv.classList.add('hidden');
        msgDiv.classList.add('hidden');
    }
}

// ─── Export ───
function exportExcel() {
    const params = buildFilterParams();
    window.location.href = apiUrl('/api/export/excel?' + params);
}

function exportTable() {
    const params = buildFilterParams();
    window.location.href = apiUrl('/api/export/table?' + params);
}

// ─── Documents ───
async function openDocuments() {
    const params = buildFilterParams();
    try {
        const resp = await apiFetch('/api/documents?' + params);
        const docs = await resp.json();
        const list = document.getElementById('docs-list');
        list.innerHTML = '';

        if (docs.length === 0) {
            list.innerHTML = '<p style="color:#a0a0a0;text-align:center;padding:20px;">No se encontraron documentos para las fechas seleccionadas.</p>';
        } else {
            docs.forEach(doc => {
                const item = document.createElement('div');
                item.className = 'docs-item';
                item.textContent = doc.name;
                item.addEventListener('click', () => {
                    window.open(apiUrl(`/api/document/open?path=${encodeURIComponent(doc.path)}`), '_blank');
                });
                list.appendChild(item);
            });
        }
        document.getElementById('docs-modal').classList.remove('hidden');
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

function closeDocsModal() {
    document.getElementById('docs-modal').classList.add('hidden');
}

// ─── Sidebar toggle ───
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    sidebar.classList.toggle('open');
}

// ─── User Management ───
let currentUsername = '';

async function loadUsers() {
    try {
        const resp = await apiFetch('/api/users');
        const users = await resp.json();
        const tbody = document.getElementById('users-tbody');
        tbody.innerHTML = '';
        currentUsername = document.getElementById('current-user')?.textContent || '';

        users.forEach(user => {
            const tr = document.createElement('tr');
            const isSelf = user.username === currentUsername;

            const tdName = document.createElement('td');
            tdName.textContent = user.username;
            if (isSelf) tdName.style.fontWeight = 'bold';

            const tdDate = document.createElement('td');
            tdDate.textContent = user.created_at ? user.created_at.split('T')[0] : '-';

            const tdActions = document.createElement('td');

            const btnPassword = document.createElement('button');
            btnPassword.className = 'btn btn-secondary';
            btnPassword.textContent = 'Contraseña';
            btnPassword.style.marginRight = '4px';
            btnPassword.style.fontSize = '12px';
            btnPassword.style.padding = '4px 8px';
            btnPassword.addEventListener('click', () => openChangePasswordModal(user.username));

            const btnDelete = document.createElement('button');
            btnDelete.className = 'btn btn-danger';
            btnDelete.textContent = 'Eliminar';
            btnDelete.style.fontSize = '12px';
            btnDelete.style.padding = '4px 8px';
            if (isSelf) {
                btnDelete.disabled = true;
                btnDelete.title = 'No puede eliminar su propio usuario';
                btnDelete.style.opacity = '0.5';
            } else {
                btnDelete.addEventListener('click', () => deleteUser(user.username));
            }

            tdActions.appendChild(btnPassword);
            if (!isSelf) tdActions.appendChild(btnDelete);

            tr.appendChild(tdName);
            tr.appendChild(tdDate);
            tr.appendChild(tdActions);
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Error loading users:', e);
    }
}

async function createUser() {
    const username = document.getElementById('new-username').value.trim();
    const password = document.getElementById('new-password').value;
    if (!username || !password) {
        alert('Ingrese usuario y contraseña');
        return;
    }
    try {
        const resp = await apiFetch('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await resp.json();
        if (resp.ok) {
            document.getElementById('new-username').value = '';
            document.getElementById('new-password').value = '';
            alert(`Usuario "${data.username}" creado exitosamente`);
            await loadUsers();
        } else {
            alert('Error: ' + (data.error || 'Error desconocido'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

function openChangePasswordModal(username) {
    document.getElementById('change-password-username').textContent = username;
    document.getElementById('change-password-new').value = '';
    document.getElementById('change-password-confirm').value = '';
    document.getElementById('change-password-modal').classList.remove('hidden');
    document.getElementById('change-password-modal').dataset.username = username;
}

function closeChangePasswordModal() {
    document.getElementById('change-password-modal').classList.add('hidden');
}

async function saveNewPassword() {
    const username = document.getElementById('change-password-modal').dataset.username;
    const newPass = document.getElementById('change-password-new').value;
    const confirmPass = document.getElementById('change-password-confirm').value;
    if (newPass !== confirmPass) {
        alert('Las contraseñas no coinciden');
        return;
    }
    if (newPass.length < 4) {
        alert('La contraseña debe tener al menos 4 caracteres');
        return;
    }
    try {
        const resp = await apiFetch(`/api/users/${encodeURIComponent(username)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_password: newPass })
        });
        const data = await resp.json();
        if (resp.ok) {
            alert('Contraseña actualizada exitosamente');
            closeChangePasswordModal();
        } else {
            alert('Error: ' + (data.error || 'Error desconocido'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function deleteUser(username) {
    if (!confirm(`¿Está seguro de eliminar el usuario "${username}"?`)) return;
    try {
        const resp = await apiFetch(`/api/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
        const data = await resp.json();
        if (resp.ok) {
            alert(`Usuario "${username}" eliminado`);
            await loadUsers();
        } else {
            alert('Error: ' + (data.error || 'Error desconocido'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

// ─── Loading ───
function showLoading(show) {
    let overlay = document.getElementById('loading-overlay');
    if (show) {
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'loading-overlay';
            overlay.className = 'loading-overlay';
            overlay.innerHTML = '<div class="loading-spinner"></div> Cargando...';
            document.querySelector('.map-container').appendChild(overlay);
        }
        overlay.classList.remove('hidden');
    } else if (overlay) {
        overlay.classList.add('hidden');
    }
}