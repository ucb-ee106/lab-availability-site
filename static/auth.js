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

    google.accounts.id.renderButton(
        buttonContainer,
        {
            theme: 'outline',
            size: 'large',
            text: 'signin_with',
            width: 300
        }
    );
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

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // Initialize Google Sign-In
    google.accounts.id.initialize({
        client_id: '22576242210-5dqoo2haju5f7t0qf5cnuq2hpbhstjpe.apps.googleusercontent.com',
        callback: handleCredentialResponse,
        auto_select: false
    });

    initializeGoogleSignIn();

    // Close modal when clicking outside of it
    window.onclick = function(event) {
        const modal = document.getElementById('signin-modal');
        if (event.target === modal) {
            closeSignInModal();
        }
    };
});
