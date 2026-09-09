import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, forkJoin } from 'rxjs';

export interface Summary {
  days: number;
  turns: number;
  sessions: number;
  escalations: number;
  blocked: number;
  ungrounded: number;
  errors: number;
  cost_usd: number;
  avg_latency_ms: number;
  cache_hit_rate: number;
  cost_per_turn_usd: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
}

export interface IntentRow {
  intent: string;
  turns: number;
  escalations: number;
  avg_latency_ms: number;
}

export interface TimeseriesRow {
  date: string;
  turns: number;
  escalations: number;
  cost_usd: number;
  avg_latency_ms: number;
}

export interface ToolRow {
  tool: string;
  calls: number;
}

export interface ReviewRow {
  session_id: string;
  at: string;
  intent: string;
  lang: string;
  grounded: boolean;
  error: string | null;
  latency_ms: number;
  cost_usd: number;
  tools_used: string[];
}

export interface HandoffRow {
  ticket_id: string;
  session_id: string;
  reason: string;
  summary: string;
  lang: string;
  status: string;
  created_at: string;
}

export interface Dashboard {
  summary: Summary;
  intents: IntentRow[];
  timeseries: TimeseriesRow[];
  tools: ToolRow[];
  review: ReviewRow[];
  handoffs: HandoffRow[];
}

@Injectable({ providedIn: 'root' })
export class AdminService {
  private readonly http = inject(HttpClient);

  /**
   * Load every panel in one go.
   *
   * forkJoin rather than six separate subscriptions in the component: the
   * dashboard is only meaningful as a complete picture, and this way the
   * template renders once instead of six times as responses trickle in.
   */
  load(days: number): Observable<Dashboard> {
    const range = { params: { days } };
    return forkJoin({
      summary: this.http.get<Summary>('/api/admin/summary', range),
      intents: this.http.get<IntentRow[]>('/api/admin/intents', range),
      timeseries: this.http.get<TimeseriesRow[]>('/api/admin/timeseries', range),
      tools: this.http.get<ToolRow[]>('/api/admin/tools', range),
      review: this.http.get<ReviewRow[]>('/api/admin/review-queue', range),
      handoffs: this.http.get<HandoffRow[]>('/api/admin/handoffs', { params: { status: 'open' } }),
    });
  }
}
