const urlInput = document.getElementById('urlInput');
const urlForm = document.getElementById('urlForm');
const webBrowser = document.getElementById('webBrowser');
const backBtn = document.getElementById('backBtn');
const refreshBtn = document.getElementById('refreshBtn');

function loadUrl(url) {
    if (!url) return;
    // Auto-add https if missing
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        url = 'https://' + url;
    }
    webBrowser.src = url;
    urlInput.value = url;
}

// Handle Enter Key
urlForm.addEventListener('submit', (e) => {
    e.preventDefault();
    loadUrl(urlInput.value);
});

// Controls
refreshBtn.addEventListener('click', () => {
    if (webBrowser.src) webBrowser.src = webBrowser.src;
});

backBtn.addEventListener('click', () => {
    // Basic back simulation
    window.alert("Note: Use the right-click menu inside the page to go back (Security Restriction)");
});

// Start on Wikipedia
loadUrl('https://en.wikipedia.org/wiki/Special:Random');