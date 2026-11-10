// Lock the whole form during a write; restore read-only controls as read-only.
export function disableControls(controls) {
  const previous = Array.from(controls, control => [control, control.disabled]);
  for (const [control] of previous) control.disabled = true;
  return () => { for (const [control, disabled] of previous) control.disabled = disabled; };
}
