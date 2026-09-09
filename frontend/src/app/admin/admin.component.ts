import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { AdminService, Dashboard, IntentRow, TimeseriesRow } from './admin.service';

/** A bar ready to render, with its geometry already resolved. */
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
}

const CHART_WIDTH = 720;
const CHART_HEIGHT = 160;

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
      error: () => {
        this.error.set('Could not load dashboard data. Is the backend running and seeded?');
        this.loading.set(false);
      },
    });
  }

  setRange(days: number): void {
    if (days === this.days()) return;
    this.days.set(days);
    this.refresh();
  }

  /** True when the window contains no turns, which is a different state from an error. */
  readonly isEmpty = computed(() => (this.data()?.summary.turns ?? 0) === 0);

  readonly intentBars = computed<Bar[]>(() => {
    const rows = this.data()?.intents ?? [];
    const max = Math.max(...rows.map((row) => row.turns), 1);
    return rows.map((row: IntentRow) => ({
      label: row.intent.replace(/_/g, ' '),
      value: row.turns,
      width: (row.turns / max) * 100,
      caption: `${row.turns} turns · ${row.avg_latency_ms} ms avg`,
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
    // A single day has no horizontal span to divide across, so it is pinned to
    // the left edge rather than dividing by zero.
    const step = rows.length > 1 ? CHART_WIDTH / (rows.length - 1) : 0;

    return rows.map((row: TimeseriesRow, index) => ({
      x: index * step,
      y: CHART_HEIGHT - (row.turns / max) * CHART_HEIGHT,
      date: row.date,
      turns: row.turns,
    }));
  });

  /** The volume line as an SVG path. */
  readonly linePath = computed(() => {
    const points = this.points();
    if (points.length < 2) return '';
    return points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  });

  /** The same line closed along the baseline, so it can be filled. */
  readonly areaPath = computed(() => {
    const points = this.points();
    if (points.length < 2) return '';
    const last = points[points.length - 1];
    return `${this.linePath()} L${last.x.toFixed(1)},${CHART_HEIGHT} L0,${CHART_HEIGHT} Z`;
  });

  readonly chartWidth = CHART_WIDTH;
  readonly chartHeight = CHART_HEIGHT;

  /**
   * Share of turns that produced a grounded answer.
   *
   * The backend reports the count of ungrounded turns rather than a rate,
   * because a rate cannot be summed across time buckets.
   */
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
