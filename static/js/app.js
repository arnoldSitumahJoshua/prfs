// Debounce helper for search inputs
function debounce(fn, ms = 150) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

// Auto-hide flash messages after 4 seconds
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => {
      el.style.transition = 'opacity .6s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 600);
    }, 4000);
  });
});