import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export type Channel = 'website' | 'shopee' | 'lazada' | 'tiktok_shop';

export const CHANNEL_LABELS: Record<Channel, string> = {
  website: 'Northlight.vn',
  shopee: 'Shopee',
  lazada: 'Lazada',
  tiktok_shop: 'TikTok Shop',
};

export interface Category {
  slug: string;
  label_vi: string;
  label_en: string;
  count: number;
}

export interface ShopProduct {
  sku: string;
  name_vi: string;
  name_en: string;
  brand: string;
  category: string;
  category_vi: string;
  category_en: string;
  price: number;
  sale_price: number | null;
  rating: number;
  review_count: number;
  stock: number;
  in_stock: boolean;
  listed_on: Channel[];
  description_vi?: string;
  description_en?: string;
  attributes?: { key: string; label_vi: string; label_en: string; value_vi: string; value_en: string }[];
}

export interface ProductPage {
  items: ShopProduct[];
  total: number;
  page: number;
  page_size: number;
}

export interface Province {
  key: string;
  name_vi: string;
  name_en: string;
}

export interface CheckoutRequest {
  items: { sku: string; quantity: number }[];
  name: string;
  phone: string;
  email: string;
  province: string;
  payment_method: 'cod' | 'bank_transfer';
}

export interface PlacedOrder {
  order_code: string;
  status: string;
  items: { sku: string; name_vi: string; name_en: string; quantity: number; unit_price: number }[];
  subtotal: number;
  shipping_fee: number;
  total: number;
  payment_method: 'cod' | 'bank_transfer';
  province: string;
  phone_masked: string;
  placed_at: string;
}

export type Sort = 'popular' | 'rating' | 'price_asc' | 'price_desc';

@Injectable({ providedIn: 'root' })
export class ShopService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/shop';

  categories(): Observable<Category[]> {
    return this.http.get<Category[]>(`${this.base}/categories`);
  }

  products(query: { category?: string; q?: string; sort: Sort; page: number }): Observable<ProductPage> {
    let params = new HttpParams().set('sort', query.sort).set('page', query.page).set('page_size', 24);
    if (query.category) params = params.set('category', query.category);
    if (query.q) params = params.set('q', query.q);
    return this.http.get<ProductPage>(`${this.base}/products`, { params });
  }

  product(sku: string): Observable<ShopProduct> {
    return this.http.get<ShopProduct>(`${this.base}/products/${encodeURIComponent(sku)}`);
  }

  provinces(): Observable<Province[]> {
    return this.http.get<Province[]>(`${this.base}/provinces`);
  }

  shipping(province: string, subtotal: number): Observable<{ shipping_fee: number; free_from: number }> {
    const params = new HttpParams().set('province', province).set('subtotal', subtotal);
    return this.http.get<{ shipping_fee: number; free_from: number }>(`${this.base}/shipping`, { params });
  }

  placeOrder(request: CheckoutRequest): Observable<PlacedOrder> {
    return this.http.post<PlacedOrder>(`${this.base}/orders`, request);
  }
}

export function effectivePrice(product: { price: number; sale_price: number | null }): number {
  return product.sale_price ?? product.price;
}

export function formatPrice(value: number | null | undefined): string {
  return value == null ? '' : new Intl.NumberFormat('vi-VN').format(value) + 'đ';
}

export function discountPercent(product: { price: number; sale_price: number | null }): number | null {
  const { price, sale_price: sale } = product;
  if (sale == null || sale >= price) return null;
  return Math.round(((price - sale) / price) * 100);
}
