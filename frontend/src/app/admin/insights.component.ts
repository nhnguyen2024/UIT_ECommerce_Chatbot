import { HttpClient } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';

interface Action { area: string; owner: string; priority: number; finding: string; evidence: string; action: string }
interface ChannelRow {
  channel: string; orders: number; gmv_vnd: number; aov_vnd: number; cancel_rate: number; return_rate: number;
  late_rate: number; fee_share: number; net_revenue_vnd: number; avg_rating: number | null; repeat_rate: number | null;
}
interface Insights {
  generated_at?: string;
  includes_simulated_history?: boolean;
  actions?: Action[];
  channels?: ChannelRow[];
  segments?: { segment: string; customers: number; revenue_vnd: number }[];
  repeat_drivers?: { driver: string; bucket: string; customers: number; repeat_rate: number }[];
  demand_gaps?: { gap_type: string; item: string; asks: number; customers: number }[];
  delivery?: { carrier: string; region: string; delivered_orders: number; avg_days: number; late_rate: number;
               tracking_chats_per_order: number; avg_rating: number | null }[];
  chatbot?: { intent: string; policy_topic: string; turns: number; escalation_rate: number; ungrounded_rate: number;
              p50_latency_ms: number }[];
  review_themes?: { theme: string; reviews: number; avg_rating: number }[];
}

const CHANNEL_LABELS: Record<string, string> = {
  website: 'Northlight.vn', shopee: 'Shopee', lazada: 'Lazada', tiktok_shop: 'TikTok Shop',
};
const AREA_LABELS: Record<string, string> = {
  delivery: 'Giao hàng', catalogue: 'Danh mục', chatbot: 'Chatbot', channel: 'Kênh bán',
  retention: 'Giữ chân khách', pricing: 'Giá',
};
const DRIVER_LABELS: Record<string, string> = {
  first_order_delivery: 'Đơn đầu tiên giao', first_order_returned: 'Đơn đầu tiên', used_chatbot: 'Đã dùng chatbot',
  first_channel: 'Kênh mua đầu tiên',
};

/**
 * Customer and marketplace insights, computed in Snowflake's gold layer and
 * written back into the app by analytics/pipeline.py.
 *
 * This page is the visible end of the loop: the chatbot and the shop produce
 * the data, the warehouse turns it into findings, and the findings come back
 * here as actions for the teams that can act on them, including the chatbot's
 * own maintainers.
 */
@Component({
  selector: 'app-insights',
  standalone: true,
  imports: [DatePipe],
  templateUrl: './insights.component.html',
  styleUrl: './insights.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class InsightsComponent {
  private readonly http = inject(HttpClient);
  readonly data = signal<Insights | null>(null);
  readonly error = signal('');

  readonly empty = computed(() => {
    const d = this.data();
    return d !== null && !d.generated_at;
  });
  readonly maxGmv = computed(() => Math.max(1, ...(this.data()?.channels ?? []).map((c) => c.gmv_vnd)));
  readonly maxSegment = computed(() => Math.max(1, ...(this.data()?.segments ?? []).map((s) => s.customers)));
  readonly maxTheme = computed(() => Math.max(1, ...(this.data()?.review_themes ?? []).map((t) => t.reviews)));
  readonly maxGap = computed(() => Math.max(1, ...(this.data()?.demand_gaps ?? []).map((g) => g.asks)));
  readonly drivers = computed(() => {
    const groups = new Map<string, { bucket: string; customers: number; repeat_rate: number }[]>();
    for (const row of this.data()?.repeat_drivers ?? []) {
      groups.set(row.driver, [...(groups.get(row.driver) ?? []), row]);
    }
    return [...groups.entries()].map(([driver, rows]) => ({ driver, label: DRIVER_LABELS[driver] ?? driver, rows }));
  });
  readonly worstDelivery = computed(() => (this.data()?.delivery ?? []).slice(0, 6));
  readonly policyTopics = computed(() =>
    (this.data()?.chatbot ?? []).filter((r) => r.intent === 'policy_question' && r.policy_topic !== '-').sort((a, b) => b.escalation_rate - a.escalation_rate),
  );

  constructor() {
    this.http.get<Insights>('/api/admin/insights').subscribe({
      next: (d) => this.data.set(d),
      error: () => this.error.set('Insights could not be loaded. Is the backend running?'),
    });
  }

  channel(name: string): string {
    return CHANNEL_LABELS[name] ?? name;
  }
  area(name: string): string {
    return AREA_LABELS[name] ?? name;
  }
  pct(v: number | null | undefined, digits = 0): string {
    return v == null ? '–' : `${(v * 100).toFixed(digits)}%`;
  }
  vnd(v: number | null | undefined): string {
    if (v == null) return '–';
    if (v >= 1e9) return `${(v / 1e9).toFixed(1).replace('.', ',')} tỷ`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(0)} tr`;
    return new Intl.NumberFormat('vi-VN').format(v) + 'đ';
  }
  num(v: number | null | undefined, digits = 1): string {
    return v == null ? '–' : v.toFixed(digits).replace('.', ',');
  }
  width(v: number, max: number): string {
    return `${Math.max(2, (v / max) * 100)}%`;
  }
}
