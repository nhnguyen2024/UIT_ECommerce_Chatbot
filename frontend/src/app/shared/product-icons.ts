/**
 * One line-art glyph per catalogue category, drawn on a 24x24 grid.
 *
 * A shopper scanning three results reads the silhouette before the words, so a
 * phone has to look like a phone. The previous card showed the first two
 * letters of the brand instead, which distinguished nothing: half the
 * catalogue is Samsung or Sony, and two grey letters look identical whether
 * they sit on a television or a charging cable.
 *
 * Stroked rather than filled, so one path inherits `currentColor` and works on
 * either theme without a second asset.
 */
export const CATEGORY_ICONS: Record<string, string> = {
  phones: 'M7.5 2.5h9a1.5 1.5 0 0 1 1.5 1.5v16a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20V4a1.5 1.5 0 0 1 1.5-1.5Z M10.5 18.6h3',
  tablets: 'M5.5 2.5h13a1.5 1.5 0 0 1 1.5 1.5v16a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 20V4a1.5 1.5 0 0 1 1.5-1.5Z M10 18.6h4',
  laptops: 'M5 5.5h14v10H5z M2.5 18.5h19 M10 15.5h4',
  audio: 'M4.5 14.5v-2.5a7.5 7.5 0 0 1 15 0v2.5 M4.5 13.5h2.2v6H6a1.5 1.5 0 0 1-1.5-1.5Z M19.5 13.5h-2.2v6H18a1.5 1.5 0 0 0 1.5-1.5Z',
  wearables: 'M9 7.5h6A1.5 1.5 0 0 1 16.5 9v6a1.5 1.5 0 0 1-1.5 1.5H9A1.5 1.5 0 0 1 7.5 15V9A1.5 1.5 0 0 1 9 7.5Z M9.5 7.5v-4h5v4 M9.5 16.5v4h5v-4 M16.5 10.8h1.4v2.4h-1.4',
  televisions: 'M3 4.5h18v12H3z M8.5 20.5h7 M12 16.5v4',
  'home-appliances': 'M5.5 2.5h13a1 1 0 0 1 1 1v17a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1v-17a1 1 0 0 1 1-1Z M4.5 7.5h15 M12 10.5a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Z M16.5 5h1',
  accessories: 'M9 2.5v5.5 M15 2.5v5.5 M6.8 8h10.4v3a5.2 5.2 0 0 1-10.4 0z M12 16.2v5.3',
};

/**
 * SKU prefix to category, for the rare card that arrives without a category.
 * `get_product_details` returns a document the model chose by SKU, and older
 * stored conversations predate the field entirely.
 */
export const SKU_CATEGORIES: Record<string, string> = {
  PHN: 'phones',
  TAB: 'tablets',
  LAP: 'laptops',
  AUD: 'audio',
  WAT: 'wearables',
  TVS: 'televisions',
  KIT: 'home-appliances',
  ACC: 'accessories',
};

/** The glyph for a product, falling back to its SKU prefix, then to accessories. */
export function iconFor(sku: string, category?: string | null): string {
  const slug = category ?? SKU_CATEGORIES[sku.slice(0, 3)];
  return CATEGORY_ICONS[slug ?? ''] ?? CATEGORY_ICONS['accessories'];
}
