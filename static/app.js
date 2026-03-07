document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.select-all-toggle').forEach(toggle => {
    const group = toggle.dataset.group;
    toggle.addEventListener('change', () => {
      document.querySelectorAll(`.group-${group}`).forEach(cb => {
        cb.checked = toggle.checked;
      });
    });
  });
});
