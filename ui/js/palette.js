// Common LEGO colours (names and values from the LDraw colour table, LDConfig.ldr; public data).
export const LEGO_COLOURS = [
  ['Black', '1B2A34'], ['Dark Bluish Gray', '595D60'], ['Light Bluish Gray', 'AFB5C7'], ['White', 'FFFFFF'],
  ['Dark Gray (old)', '6B5A5A'], ['Light Gray (old)', '9BA19D'], ['Red', 'B40000'], ['Dark Red', '720E0F'],
  ['Bright Pink', 'E4ADC8'], ['Dark Pink', 'C870A0'], ['Magenta', '923978'], ['Orange', 'D67923'],
  ['Bright Light Orange', 'F8BB3D'], ['Yellow', 'FAC80A'], ['Bright Light Yellow', 'FFF03A'], ['Tan', 'E4CD9E'],
  ['Dark Tan', '958A73'], ['Nougat', 'D09168'], ['Medium Nougat', 'AA7D55'], ['Reddish Brown', '5F3109'],
  ['Dark Brown', '352100'], ['Lime', 'BBE90B'], ['Bright Green', '58AB41'], ['Green', '00852B'],
  ['Dark Green', '184632'], ['Olive Green', '9B9A5A'], ['Sand Green', 'A0BCAC'], ['Dark Turquoise', '069D9F'],
  ['Medium Azure', '36AEBF'], ['Dark Azure', '078BC9'], ['Medium Blue', '5A93DB'], ['Blue', '1E5AA8'],
  ['Dark Blue', '19325A'], ['Sand Blue', '70819A'], ['Bright Light Blue', '9FC3E9'], ['Medium Lavender', 'AC78BA'],
  ['Dark Purple', '3F3691'], ['Lavender', 'E1D5ED'], ['Light Flesh', 'F6D7B3'], ['Flat Silver', '898788'],
  ['Pearl Gold', 'AA7F2E'], ['Chrome Silver', 'E0E0E0'], ['Trans Clear', 'FCFCFC'], ['Trans Red', 'C91A09'],
  ['Trans Light Blue', 'AEEFEC'], ['Trans Neon Green', 'F8F184'],
];

export function hexToRgb(hex) {
  const v = parseInt(hex.replace('#', ''), 16);
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
}

export function rgbToHex(rgb) {
  return '#' + rgb.slice(0, 3).map(c => Math.round(Math.max(0, Math.min(1, c)) * 255).toString(16).padStart(2, '0')).join('');
}
