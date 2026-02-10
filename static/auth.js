// Global variables
let currentUser = window.preloadedUser || null;
let pendingQueueType = null;

// ---------------------------------------------------------------------------
// Google Sign-In initialisation
// ---------------------------------------------------------------------------

// Called via onload= attribute on the Google script tag (preferred), or
// from the DOMContentLoaded fallback below.
function initGoogleSignIn() {
    if (typeof google === 'undefined' || !google.accounts || !google.accounts.id) return;

    google.accounts.id.initialize({
        client_id: '22576242210-5dqoo2haju5f7t0qf5cnuq2hpbhstjpe.apps.googleusercontent.com',
        callback: handleCredentialResponse,
        auto_select: false
    });

    // Check if user is already logged in (skip if preloaded)
    if (!currentUser) {
        checkAuthStatus();
    } else {
        updateUserStatus();
    }
}

// Check current authentication status
async function checkAuthStatus() {
    try {
        const response = await fetch('/api/auth/user');
        if (response.ok) {
            currentUser = await response.json();
        } else {
            currentUser = null;
        }
    } catch (error) {
        currentUser = null;
    }
    updateUserStatus();
}

// Handle credential response from Google
async function handleCredentialResponse(response) {
    try {
        const result = await fetch('/api/auth/google', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credential: response.credential })
        });

        const data = await result.json();

        if (result.ok) {
            currentUser = data.user;
            updateUserStatus();
            closeSignInModal();
            showMessage('Successfully signed in!', 'success');

            // If there was a pending queue action, execute it now
            if (pendingQueueType) {
                const queueType = pendingQueueType;
                pendingQueueType = null;
                addToQueueDirect(queueType);
            }
        } else {
            showMessage(data.error || 'Authentication failed', 'error');
        }
    } catch (error) {
        showMessage('Authentication error. Please try again.', 'error');
    }
}

// Logout function
async function logout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        currentUser = null;

        // Sign out from Google
        google.accounts.id.disableAutoSelect();

        updateUserStatus();
        showMessage('Logged out successfully', 'success');
    } catch (error) {
        // silently fail
    }
}

// Update user status indicator
function updateUserStatus() {
    const userStatus = document.getElementById('user-status');
    if (!userStatus) return;

    if (currentUser) {
        userStatus.innerHTML = `
            <div class="user-info">
                <img src="${currentUser.picture}" alt="${currentUser.name}" class="user-avatar">
                <span class="user-name">${currentUser.name}</span>
                <button onclick="logout()" class="logout-button">Logout</button>
            </div>
        `;
    } else {
        userStatus.innerHTML = '';
    }
}

// ---------------------------------------------------------------------------
// Sign-in modal (with basic accessibility)
// ---------------------------------------------------------------------------

function showSignInModal() {
    const modal = document.getElementById('signin-modal');
    modal.style.display = 'block';

    // Render Google Sign-In button in the modal
    const buttonContainer = document.getElementById('google-signin-button');
    buttonContainer.innerHTML = '';

    if (typeof google === 'undefined' || !google.accounts || !google.accounts.id) {
        buttonContainer.innerHTML = '<p style="color: red;">Error: Sign-in unavailable. Please refresh the page.</p>';
        return;
    }

    try {
        google.accounts.id.renderButton(buttonContainer, {
            theme: 'outline',
            size: 'large',
            text: 'signin_with',
            width: 300
        });
    } catch (error) {
        buttonContainer.innerHTML = '<p style="color: red;">Error loading sign-in button. Please refresh the page.</p>';
    }

    // Move focus into modal
    const closeBtn = modal.querySelector('.close');
    if (closeBtn) closeBtn.focus();
}

function closeSignInModal() {
    const modal = document.getElementById('signin-modal');
    modal.style.display = 'none';
    pendingQueueType = null;
}

// ---------------------------------------------------------------------------
// Auto-refresh (polls /api/lab-data every 10 s)
// ---------------------------------------------------------------------------

// Track queue visibility state for change detection
let _prevQueueState = window._initialQueueState || null;

async function refreshLabData() {
    try {
        const resp = await fetch('/api/lab-data');
        if (!resp.ok) return;
        const data = await resp.json();

        // Check if queue visibility changed - reload page to update queue sections
        if (_prevQueueState) {
            const newState = data.status;
            if (_prevQueueState.turtlebot !== newState.show_turtlebot_queue ||
                _prevQueueState.ur7e !== newState.show_ur7e_queue ||
                _prevQueueState.active !== newState.queue_active) {
                location.reload();
                return;
            }
        }
        _prevQueueState = {
            turtlebot: data.status.show_turtlebot_queue,
            ur7e: data.status.show_ur7e_queue,
            active: data.status.queue_active
        };

        // Update lab status banner
        const banner = document.getElementById('lab-status-banner');
        if (banner) banner.style.background = data.status.color;

        const stateEl = document.getElementById('lab-state');
        if (stateEl) stateEl.textContent = data.status.state;

        const detailsEl = document.getElementById('lab-details');
        if (detailsEl) {
            detailsEl.innerHTML =
                data.status.turtlebots_available +
                ' Turtlebot' + (data.status.turtlebots_available !== 1 ? 's' : '') + ' Open<br>' +
                data.status.ur7es_available +
                ' UR7e' + (data.status.ur7es_available !== 1 ? 's' : '') + ' Open';
        }

        // Refresh SVG (cache-buster forces re-fetch; server responds quickly due to cache)
        const svgImg = document.getElementById('lab-svg');
        if (svgImg) {
            svgImg.src = '/lab_room.svg?' + Date.now();
        }
    } catch (e) {
        // Silently fail - will retry next interval
    }
}

// Start auto-refresh
let _autoRefreshId = null;
function startAutoRefresh() {
    if (_autoRefreshId) return;
    _autoRefreshId = setInterval(refreshLabData, 10000);
}

// ---------------------------------------------------------------------------
// Queue operations
// ---------------------------------------------------------------------------

async function addToQueue(queueType) {
    if (!currentUser) {
        pendingQueueType = queueType;
        showSignInModal();
        return;
    }
    addToQueueDirect(queueType);
}

async function addToQueueDirect(queueType) {
    try {
        const response = await fetch('/api/queue/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queue_type: queueType })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message + '. When a station opens, you\'ll get an email. You have 5 minutes to click the confirmation link and log into the computer.', 'success');
            // Refresh data then reload to show queue table changes
            await refreshLabData();
            setTimeout(() => location.reload(), 3000);
        } else {
            showMessage(data.error || 'Failed to join queue', 'error');
        }
    } catch (error) {
        showMessage('Error joining queue. Please try again.', 'error');
    }
}

// Remove from queue function (admin only)
async function removeFromQueue(queueType, email, name) {
    if (!currentUser) {
        showMessage('Please sign in first', 'error');
        return;
    }

    if (!confirm(`Are you sure you want to remove ${name} from the ${queueType} queue?`)) {
        return;
    }

    try {
        const response = await fetch('/api/queue/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queue_type: queueType, email: email })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message, 'success');
            await refreshLabData();
            setTimeout(() => location.reload(), 800);
        } else {
            showMessage(data.error || 'Failed to remove from queue', 'error');
        }
    } catch (error) {
        showMessage('Error removing from queue. Please try again.', 'error');
    }
}

// Move in queue function (admin only)
async function moveInQueue(queueType, email, direction) {
    if (!currentUser) {
        showMessage('Please sign in first', 'error');
        return;
    }

    try {
        const response = await fetch('/api/queue/reorder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queue_type: queueType, email: email, direction: direction })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message, 'success');
            await refreshLabData();
            setTimeout(() => location.reload(), 500);
        } else {
            showMessage(data.error || 'Failed to reorder queue', 'error');
        }
    } catch (error) {
        showMessage('Error reordering queue. Please try again.', 'error');
    }
}

// Show message to user
function showMessage(message, type) {
    // Remove any existing messages
    const existingMessage = document.querySelector('.message-toast');
    if (existingMessage) existingMessage.remove();

    const messageDiv = document.createElement('div');
    messageDiv.className = `message-toast message-${type}`;
    messageDiv.textContent = message;
    document.body.appendChild(messageDiv);

    setTimeout(() => messageDiv.remove(), 5000);
}

// ---------------------------------------------------------------------------
// Drag and drop for queue reordering (admin)
// ---------------------------------------------------------------------------

let draggedRow = null;
let draggedOverRow = null;

function initializeDragAndDrop() {
    const queueRows = document.querySelectorAll('.queue-row');
    queueRows.forEach(row => {
        row.addEventListener('dragstart', handleDragStart);
        row.addEventListener('dragover', handleDragOver);
        row.addEventListener('drop', handleDrop);
        row.addEventListener('dragenter', handleDragEnter);
        row.addEventListener('dragleave', handleDragLeave);
        row.addEventListener('dragend', handleDragEnd);
    });
}

function handleDragStart(e) {
    draggedRow = this;
    this.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', this.innerHTML);
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    return false;
}

function handleDragEnter(e) {
    if (this !== draggedRow && this.dataset.queueType === draggedRow.dataset.queueType) {
        this.classList.add('drag-over');
        draggedOverRow = this;
    }
}

function handleDragLeave() {
    this.classList.remove('drag-over');
}

function handleDrop(e) {
    e.stopPropagation();

    if (draggedRow !== this && this.dataset.queueType === draggedRow.dataset.queueType) {
        const queueType = this.dataset.queueType;
        const email = draggedRow.dataset.email;
        const newIndex = parseInt(this.dataset.index);
        repositionInQueue(queueType, email, newIndex);
    }
    return false;
}

function handleDragEnd() {
    document.querySelectorAll('.queue-row').forEach(row => {
        row.classList.remove('dragging');
        row.classList.remove('drag-over');
    });
    draggedRow = null;
    draggedOverRow = null;
}

async function repositionInQueue(queueType, email, newIndex) {
    if (!currentUser) {
        showMessage('Please sign in first', 'error');
        return;
    }

    try {
        const response = await fetch('/api/queue/reposition', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queue_type: queueType, email: email, new_index: newIndex })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message, 'success');
            await refreshLabData();
            setTimeout(() => location.reload(), 500);
        } else {
            showMessage(data.error || 'Failed to reorder queue', 'error');
        }
    } catch (error) {
        showMessage('Error reordering queue. Please try again.', 'error');
    }
}

// ---------------------------------------------------------------------------
// Station override functions (admin)
// ---------------------------------------------------------------------------

function clearActiveUser(station) {
    showMessage('Clear Active User functionality coming soon', 'error');
}

async function setOverride(station, occupied) {
    if (!currentUser) {
        showMessage('Please sign in first', 'error');
        return;
    }

    try {
        const response = await fetch('/api/station/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ station: station, override_occupied: occupied })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message, 'success');
            updateOverrideButtonStates(station, occupied);
            await refreshLabData();
        } else {
            showMessage(data.error || 'Failed to set override', 'error');
        }
    } catch (error) {
        showMessage('Error setting override. Please try again.', 'error');
    }
}

async function clearOverride(station) {
    if (!currentUser) {
        showMessage('Please sign in first', 'error');
        return;
    }

    try {
        const response = await fetch('/api/station/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ station: station, override_occupied: null })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message, 'success');
            updateOverrideButtonStates(station, null);
            await refreshLabData();
        } else {
            showMessage(data.error || 'Failed to clear override', 'error');
        }
    } catch (error) {
        showMessage('Error clearing override. Please try again.', 'error');
    }
}

function updateOverrideButtonStates(station, state) {
    const item = document.querySelector(`.override-item[data-station="${station}"]`);
    if (!item) return;

    item.querySelectorAll('.override-btn').forEach(btn => btn.classList.remove('active'));

    if (state === true) {
        item.querySelector('.override-occupied').classList.add('active');
    } else if (state === false) {
        item.querySelector('.override-available').classList.add('active');
    } else {
        item.querySelector('.override-clear').classList.add('active');
    }
}

async function loadOverrideStates() {
    try {
        const response = await fetch('/api/station/overrides');
        if (response.ok) {
            const data = await response.json();
            const overrides = data.overrides || {};

            document.querySelectorAll('.override-item').forEach(item => {
                const station = item.dataset.station;
                if (overrides[station] !== undefined) {
                    updateOverrideButtonStates(parseInt(station), overrides[station]);
                } else {
                    updateOverrideButtonStates(parseInt(station), null);
                }
            });
        }
    } catch (error) {
        // silently fail
    }
}

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    // Fallback: if onload on the script tag didn't fire (e.g. already cached),
    // try to initialise now.
    if (typeof google !== 'undefined' && google.accounts && google.accounts.id) {
        initGoogleSignIn();
    }

    // Close modal on backdrop click or Escape key
    window.addEventListener('click', (event) => {
        const modal = document.getElementById('signin-modal');
        if (event.target === modal) closeSignInModal();
    });
    window.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeSignInModal();
    });

    // Initialize drag and drop for queue management
    initializeDragAndDrop();

    // Load current override states (admin page)
    loadOverrideStates();

    // Start auto-refreshing SVG + status every 10 s
    startAutoRefresh();
});
