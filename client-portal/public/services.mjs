// Service menus per client business, keyed by the business slug in Supabase.
// Codes must match what that business's voice agent saves
// Canonical codes live in studio-agent/clients/<slug>/client.json.
export const SERVICE_MENUS = {
  'property-services': [
    ['water_damage', 'Water damage inquiry'],
    ['sewage_cleanup', 'Sewage cleanup inquiry'],
    ['drain_cleaning', 'Drain cleaning inquiry'],
    ['mold_inquiry', 'Mold assessment inquiry'],
    ['fire_smoke', 'Fire or smoke cleanup inquiry'],
    ['storm_damage', 'Storm damage inquiry'],
    ['camera_inspection', 'Camera inspection inquiry'],
    ['general_inquiry', 'General inquiry'],
  ],
  'sampleclub': [
    ['court_rental', 'Court rental inquiry'],
    ['lesson', 'Lesson or clinic inquiry'],
    ['membership', 'Membership inquiry'],
    ['open_play', 'Open play or league inquiry'],
    ['private_event', 'Private event or party inquiry'],
    ['general_inquiry', 'General inquiry'],
  ],
  'sample-studio': [
    ['recording', 'Recording session (with engineer)'],
    ['recording_light_mix', 'Recording + light mix'],
    ['music_rental', 'Music room rental (no engineer)'],
    ['mixing', 'Mixing'],
    ['mastering', 'Mastering'],
    ['photo_rental', 'Photo space rental'],
    ['photoshoot', 'Photoshoot (with photographer)'],
  ],
};

const readable = code => {
  const text = String(code ?? '').replaceAll('_', ' ').trim();
  return text ? text[0].toUpperCase() + text.slice(1) : 'Not provided';
};

// The menu for a business, plus any service already on its requests,
// so an unfamiliar value is still shown rather than silently replaced.
export function serviceMenu(slug, seenCodes = []) {
  const menu = [...(SERVICE_MENUS[slug] ?? [])];
  for (const code of seenCodes) {
    if (code && !menu.some(([c]) => c === code)) menu.push([code, readable(code)]);
  }
  return menu;
}

export function serviceLabel(code, menu = []) {
  return menu.find(([c]) => c === code)?.[1] ?? readable(code);
}
