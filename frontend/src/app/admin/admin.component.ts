import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { AdminService, Dashboard, IntentRow, TimeseriesRow } from './admin.service';

/** A bar with its geometry already resolved. */
interface Bar {
  label: string;
  value: number;
  width: number;
  caption: string;
}

/** One point on the volume chart, in SVG user units. */
interface Point {
  x: number;
  y: number;
  date: string;
  turns: number;
  cost: number;
}

const CHART_WIDTH = 720;
const CHART_HEIGHT = 150;

@Component({
  selector: 'app-admin',
  standalone: true,
  templateUrl: './admin.component.html',
  styleUrl: './admin.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdminComponent {
  private readonly admin = inject(AdminService);

  readonly days = signal(7);
  readonly data = signal<Dashboard | null>(null);
  readonly loading = signal(true);
  readonly error = signal('');

  /** Index of the hovered point on the volume chart, or null. */
  readonly hovered = signal<number | null>(null);

  readonly chartWidth = CHART_WIDTH;
  readonly chartHeight = CHART_HEIGHT;

  constructor() {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.error.set('');
    this.admin.load(this.days()).subscribe({
      next: (dashboard) => {
        this.data.set(dashboard);
        this.loading.set(false);
      },
      error: (response: { status?: number }) => {
        this.error.set(
          response?.status === 503
            ? 'The database is unreachable. Check MONGODB_URI and your Atlas network access list.'
            : 'Could not load dashboard data. Is the backend running?',
        );
        this.loading.set(false);
      },
    });
  }

  setRange(days: number): void {
    if (days === this.days()) return;
    this.days.set(days);
    this.hovered.set(null);
    this.refresh();
  }

  /** No turns in the window is an empty state, not an error. */
  readonly isEmpty = computed(() => (this.data()?.summary.turns ?? 0) === 0);

  readonly intentBars = computed<Bar[]>(() => {
    const rows = this.data()?.intents ?? [];
    const max = Math.max(...rows.map((row) => row.turns), 1);
    return rows.map((row: IntentRow) => ({
      label: row.intent.replace(/_/g, ' '),
      value: row.turns,
      width: (row.turns / max) * 100,
      caption: `${row.turns} · ${row.avg_latency_ms} ms`,
    }));
  });

  readonly toolBars = computed<Bar[]>(() => {
    const rows = this.data()?.tools ?? [];
    const max = Math.max(...rows.map((row) => row.calls), 1);
    return rows.map((row) => ({
      label: row.tool.replace(/_/g, ' '),
      value: row.calls,
      width: (row.calls / max) * 100,
      caption: `${row.calls} calls`,
    }));
  });

  readonly points = computed<Point[]>(() => {
    const rows = this.data()?.timeseries ?? [];
    if (!rows.length) return [];

    const max = Math.max(...rows.map((row) => row.turns), 1);
    // One day has no horizontal span to divide across, so it pins to the left
    // edge rather than dividing by zero.
    const step = rows.length > 1 ? CHART_WIDTH / (rows.length - 1) : 0;

    return rows.map((row: TimeseriesRow, index) => ({
      x: index * step,
      y: CHART_HEIGHT - (row.turns / max) * CHART_HEIGHT,
      date: row.date,
      turns: row.turns,
      cost: row.cost_usd,
    }));
  });

  readonly linePath = computed(() => {
    const points = this.points();
    if (points.length < 2) return '';
    return points
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      .join(' ');
  });

  readonly areaPath = computed(() => {
    const points = this.points();
    if (points.length < 2) return '';
    const last = points[points.length - 1];
    return `${this.linePath()} L${last.x.toFixed(1)},${CHART_HEIGHT} L0,${CHART_HEIGHT} Z`;
  });

  readonly hoveredPoint = computed(() => {
    const index = this.hovered();
    return index == null ? null : (this.points()[index] ?? null);
  });

  /**
   * Track the nearest point to the cursor.
   *
   * The hit area is the whole chart rather than each marker, because an 8px
   * circle is a hard target and a reader expects the tooltip to follow their
   * cursor along the line.
   */
  onChartMove(event: MouseEvent, element: HTMLElement): void {
    const points = this.points();
    if (!points.length) return;

    const bounds = element.getBoundingClientRect();
    if (!bounds.width) return;

    const ratio = (event.clientX - bounds.left) / bounds.width;
    const index = Math.round(ratio * (points.length - 1));
    this.hovered.set(Math.min(Math.max(index, 0), points.length - 1));
  }

  clearHover(): void {
    this.hovered.set(null);
  }

  /**
   * Percent of the chart width, for positioning the tooltip in CSS.
   *
   * Clamped away from both edges: the tooltip is centred on the point, so at
   * the first and last day half of it would hang outside the panel. Losing a
   * few pixels of alignment is a better trade than a clipped tooltip.
   */
  hoverLeft(): number {
    const point = this.hoveredPoint();
    if (!point) return 0;
    return Math.min(Math.max((point.x / CHART_WIDTH) * 100, 7), 93);
  }

  readonly groundedRate = computed(() => {
    const summary = this.data()?.summary;
    if (!summary?.turns) return 1;
    return (summary.turns - summary.ungrounded) / summary.turns;
  });

  readonly escalationRate = computed(() => {
    const summary = this.data()?.summary;
    if (!summary?.turns) return 0;
    return summary.escalations / summary.turns;
  });

  /** Cache health is the metric most likely to regress without any symptom. */
  readonly cacheHealthy = computed(() => (this.data()?.summary.cache_hit_rate ?? 0) >= 0.4);

  percent(value: number): string {
    return `${(value * 100).toFixed(1)}%`;
  }

  money(value: number): string {
    return `$${value.toFixed(value < 1 ? 4 : 2)}`;
  }

  compact(value: number): string {
    return new Intl.NumberFormat('en', { notation: 'compact' }).format(value);
  }

  shortTime(iso: string): string {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
  }
}
