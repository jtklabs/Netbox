(function () {
  const initialize = () => {
    document.querySelectorAll('form[data-upgrade-timing]').forEach(form => {
      const zone = form.querySelector('[name="time_zone"]');
      const start = form.querySelector('[name="scheduled_at"]');
      const end = form.querySelector('[name="start_before"]');
      const now = form.querySelector('[data-schedule-now]');
      if (!zone || !start || !now) return;

      if (form.dataset.browserTimeZone === 'true' && !start.value && !end?.value) {
        try {
          const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
          if (Array.from(zone.options).some(option => option.value === browserZone)) {
            if (zone.tomselect) zone.tomselect.setValue(browserZone);
            else zone.value = browserZone;
            zone.dispatchEvent(new Event('change', {bubbles: true}));
          }
        } catch (_) {
          // Keep the server's New York default when browser detection fails.
        }
      }

      now.addEventListener('click', () => {
        const parts = new Intl.DateTimeFormat('en-US', {
          timeZone: zone.value || 'America/New_York', calendar: 'gregory', numberingSystem: 'latn',
          year: 'numeric', month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
        }).formatToParts(new Date());
        const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
        start.value = `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}:${values.second}`;
        start.dispatchEvent(new Event('input', {bubbles: true}));
        start.dispatchEvent(new Event('change', {bubbles: true}));
      });
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
