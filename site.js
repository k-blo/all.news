// Shared by every page (feed, archive, hub, legal, settings).

// "Cookie settings" reopens Google's consent message (the AdSense CMP, set up
// under AdSense → Privacy & messaging). Until that CMP has loaded, the link
// simply navigates to /cookies#manage, which explains the other options.
window.googlefc = window.googlefc || {};
window.googlefc.callbackQueue = window.googlefc.callbackQueue || [];
document.addEventListener("click", (e) => {
  const link = e.target.closest(".cookie-settings");
  const fc = window.googlefc;
  if (!link || typeof fc.showRevocationMessage !== "function") return;
  e.preventDefault();
  fc.callbackQueue.push(fc.showRevocationMessage);
});
