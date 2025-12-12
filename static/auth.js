// Global variables
let currentUser = null;
let pendingQueueType = null;

// Initialize Google Sign-In
function initializeGoogleSignIn() {
    // Check if user is already logged in
    checkAuthStatus();
}

// Check current authentication status
async function checkAuthStatus() {
    try {
        const response = await fetch('/api/auth/user');
        if (response.ok) {
            currentUser = await response.json();
            updateUserStatus();
        } else {
            currentUser = null;
            updateUserStatus();
        }
    } catch (error) {
        console.error('Error checking auth status:', error);
        currentUser = null;
        updateUserStatus();
    }
}

// Handle credential response from Google
async function handleCredentialResponse(response) {
    try {
        const result = await fetch('/api/auth/google', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                credential: response.credential
            })
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
        console.error('Error during authentication:', error);
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
        console.error('Error during logout:', error);
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

// Show sign-in modal
function showSignInModal() {
    const modal = document.getElementById('signin-modal');
    modal.style.display = 'block';

    // Render Google Sign-In button in the modal
    const buttonContainer = document.getElementById('google-signin-button');
    buttonContainer.innerHTML = ''; // Clear previous button

    // Check if Google Sign-In is loaded
    if (typeof google === 'undefined' || !google.accounts || !google.accounts.id) {
        console.error('Google Sign-In library not loaded');
        buttonContainer.innerHTML = '<p style="color: red;">Error: Sign-in unavailable. Please refresh the page.</p>';
        return;
    }

    try {
        google.accounts.id.renderButton(
            buttonContainer,
            {
                theme: 'outline',
                size: 'large',
                text: 'signin_with',
                width: 300
            }
        );
    } catch (error) {
        console.error('Error rendering Google Sign-In button:', error);
        buttonContainer.innerHTML = '<p style="color: red;">Error loading sign-in button. Please refresh the page.</p>';
    }
}

// Close sign-in modal
function closeSignInModal() {
    const modal = document.getElementById('signin-modal');
    modal.style.display = 'none';
    pendingQueueType = null;
}

// Add to queue function (triggered by button click)
async function addToQueue(queueType) {
    if (!currentUser) {
        // User not signed in, show modal
        pendingQueueType = queueType;
        showSignInModal();
        return;
    }

    // User is signed in, add to queue directly
    addToQueueDirect(queueType);
}

// Add to queue directly (when authenticated)
async function addToQueueDirect(queueType) {
    try {
        const response = await fetch('/api/queue/add', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                queue_type: queueType
            })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message, 'success');
            // Reload page to show updated queue
            setTimeout(() => location.reload(), 1500);
        } else {
            showMessage(data.error || 'Failed to join queue', 'error');
        }
    } catch (error) {
        console.error('Error adding to queue:', error);
        showMessage('Error joining queue. Please try again.', 'error');
    }
}

// Remove from queue function (admin only)
async function removeFromQueue(queueType, email, name) {
    if (!currentUser) {
        showMessage('Please sign in first', 'error');
        return;
    }

    // Confirm removal
    if (!confirm(`Are you sure you want to remove ${name} from the ${queueType} queue?`)) {
        return;
    }

    try {
        const response = await fetch('/api/queue/remove', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                queue_type: queueType,
                email: email
            })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message, 'success');
            // Reload page to show updated queue
            setTimeout(() => location.reload(), 1000);
        } else {
            showMessage(data.error || 'Failed to remove from queue', 'error');
        }
    } catch (error) {
        console.error('Error removing from queue:', error);
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
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                queue_type: queueType,
                email: email,
                direction: direction
            })
        });

        const data = await response.json();

        if (response.ok) {
            showMessage(data.message, 'success');
            // Reload page to show updated queue
            setTimeout(() => location.reload(), 800);
        } else {
            showMessage(data.error || 'Failed to reorder queue', 'error');
        }
    } catch (error) {
        console.error('Error reordering queue:', error);
        showMessage('Error reordering queue. Please try again.', 'error');
    }
}

// Show message to user
function showMessage(message, type) {
    // Remove any existing messages
    const existingMessage = document.querySelector('.message-toast');
    if (existingMessage) {
        existingMessage.remove();
    }

    const messageDiv = document.createElement('div');
    messageDiv.className = `message-toast message-${type}`;
    messageDiv.textContent = message;
    document.body.appendChild(messageDiv);

    // Remove message after 3 seconds
    setTimeout(() => {
        messageDiv.remove();
    }, 3000);
}

// Initialize Google Sign-In when library is ready
function initGoogleSignIn() {
    if (typeof google !== 'undefined' && google.accounts && google.accounts.id) {
        google.accounts.id.initialize({
            client_id: '22576242210-5dqoo2haju5f7t0qf5cnuq2hpbhstjpe.apps.googleusercontent.com',
            callback: handleCredentialResponse,
            auto_select: false
        });

        initializeGoogleSignIn();
    } else {
        console.error('Google Sign-In library not loaded');
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // Wait for Google Sign-In library to load
    if (typeof google !== 'undefined' && google.accounts) {
        initGoogleSignIn();
    } else {
        // Poll for Google library to be ready
        let attempts = 0;
        const checkGoogle = setInterval(() => {
            attempts++;
            if (typeof google !== 'undefined' && google.accounts && google.accounts.id) {
                clearInterval(checkGoogle);
                initGoogleSignIn();
            } else if (attempts > 50) { // Stop after 5 seconds
                clearInterval(checkGoogle);
                console.error('Google Sign-In library failed to load');
            }
        }, 100);
    }

    // Close modal when clicking outside of it
    window.onclick = function(event) {
        const modal = document.getElementById('signin-modal');
        if (event.target === modal) {
            closeSignInModal();
        }
    };
});
