document.querySelectorAll('[data-watermark-control]').forEach((control) => {
  const checkbox = control.querySelector('input[name="watermark"]');
  const options = control.querySelector('[data-watermark-options]');
  if (!checkbox || !options) return;
  const update = () => { options.hidden = !checkbox.checked; };
  checkbox.addEventListener('change', update);
  update();
});
