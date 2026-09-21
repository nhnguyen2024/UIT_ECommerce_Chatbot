import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { HOANG_SA, MAP, NEIGHBOURS_PATH, TRUONG_SA, VIETNAM_PATH } from './vietnam-map';

/** One place on the parcel's way, as the order tool returns it. */
export interface TrackingStop {
  key: string;
  name: string;
  kind: 'warehouse' | 'hub' | 'destination';
  lat: number;
  lon: number;
  arrived_at: string | null;
}

export interface Tracking {
  order_code: string;
  channel_label: string;
  status: string;
  carrier: string | null;
  estimated_delivery: string | null;
  stops: TrackingStop[];
}

interface Point {
  x: number;
  y: number;
}

/** A marker on the map. Stops closer than a few pixels share one. */
interface Marker extends Point {
  state: 'done' | 'current' | 'ahead';
  kind: TrackingStop['kind'];
  label: string;
}

const STATUS_LABELS: Record<string, { vi: string; en: string }> = {
  pending: { vi: 'Chờ xác nhận', en: 'Awaiting confirmation' },
  confirmed: { vi: 'Đã xác nhận', en: 'Confirmed' },
  packing: { vi: 'Đang đóng gói', en: 'Packing' },
  shipped: { vi: 'Đang vận chuyển', en: 'In transit' },
  out_for_delivery: { vi: 'Đang giao hàng', en: 'Out for delivery' },
  delivered: { vi: 'Đã giao', en: 'Delivered' },
  cancelled: { vi: 'Đã hủy', en: 'Cancelled' },
  returned: { vi: 'Đã hoàn trả', en: 'Returned' },
};

/** Statuses where the parcel is moving, so the leg ahead is drawn as live. */
const MOVING = new Set(['shipped', 'out_for_delivery']);

/** Map units; markers closer than this merge so a city's three stops stay legible. */
const MERGE_DISTANCE = 14;

function project(lat: number, lon: number): Point {
  return { x: (lon - MAP.lonMin) * MAP.cos * MAP.scale, y: (MAP.latMax - lat) * MAP.scale };
}

function centroid(points: [number, number][]): Point {
  const x = points.reduce((sum, [px]) => sum + px, 0) / points.length;
  const y = points.reduce((sum, [, py]) => sum + py, 0) / points.length;
  return { x, y };
}

function polyline(points: Point[]): string {
  return points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
}

/**
 * A verified order's route on a map of Vietnam.
 *
 * Drawn from committed SVG geometry (see scripts/build_vietnam_map.py) rather
 * than a tile service: no third-party request carries the shopper's route,
 * nothing breaks offline at a demo, and the map shows Hoàng Sa and Trường Sa,
 * which common tile sets omit or label otherwise.
 *
 * Positions are hub-level, because that is what carrier tracking reports; the
 * map never pretends to know where the courier is between two scans.
 */
@Component({
  selector: 'app-order-map',
  standalone: true,
  templateUrl: './order-map.component.html',
  styleUrl: './order-map.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class OrderMapComponent {
  readonly tracking = input.required<Tracking>();
  readonly lang = input<'vi' | 'en'>('vi');

  readonly vietnamPath = VIETNAM_PATH;
  readonly neighboursPath = NEIGHBOURS_PATH;
  readonly hoangSa = HOANG_SA;
  readonly truongSa = TRUONG_SA;
  readonly hoangSaLabel = centroid(HOANG_SA);
  readonly truongSaLabel = centroid(TRUONG_SA);

  readonly points = computed(() => this.tracking().stops.map((stop) => project(stop.lat, stop.lon)));

  /** Index of the last stop reached, or -1 if none has been. */
  readonly currentIndex = computed(() => {
    const stops = this.tracking().stops;
    let index = -1;
    stops.forEach((stop, i) => {
      if (stop.arrived_at) index = i;
    });
    return index;
  });

  readonly moving = computed(() => MOVING.has(this.tracking().status));
  readonly delivered = computed(() => {
    const stops = this.tracking().stops;
    return stops.length > 0 && !!stops[stops.length - 1].arrived_at;
  });

  /** The legs already travelled, the one under way, and the rest. */
  readonly travelled = computed(() => polyline(this.points().slice(0, this.currentIndex() + 1)));
  readonly activeLeg = computed(() => {
    const index = this.currentIndex();
    const points = this.points();
    if (!this.moving() || index < 0 || index >= points.length - 1) return '';
    return polyline(points.slice(index, index + 2));
  });
  readonly ahead = computed(() => {
    const start = this.currentIndex() + (this.activeLeg() ? 1 : 0);
    return polyline(this.points().slice(Math.max(start, 0)));
  });

  readonly markers = computed<Marker[]>(() => {
    const current = this.currentIndex();
    const markers: Marker[] = [];
    this.tracking().stops.forEach((stop, index) => {
      const point = this.points()[index];
      const state: Marker['state'] = index < current ? 'done' : index === current ? 'current' : 'ahead';
      const near = markers.find((marker) => Math.hypot(marker.x - point.x, marker.y - point.y) < MERGE_DISTANCE);
      if (near) {
        // Keep the most informative state and the later stop's kind.
        if (state === 'current' || (state === 'done' && near.state === 'ahead')) near.state = state;
        if (stop.kind === 'destination') near.kind = stop.kind;
        near.label = `${near.label} · ${stop.name}`;
        return;
      }
      markers.push({ ...point, state, kind: stop.kind, label: stop.name });
    });
    return markers;
  });

  /**
   * The part of the map worth showing: the route with some margin.
   *
   * A Hanoi-to-Hai Phong parcel would be a dot on a whole-country map, so the
   * view zooms to the route. A route spanning most of the country gets the full
   * frame, archipelagos included.
   */
  readonly viewBox = computed(() => {
    const points = this.points();
    if (!points.length) return `0 0 ${MAP.width} ${MAP.height}`;
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    let width = Math.max(Math.max(...xs) - Math.min(...xs), 1);
    let height = Math.max(Math.max(...ys) - Math.min(...ys), 1);
    if (height > MAP.height * 0.45) return `0 0 ${MAP.width} ${MAP.height}`;

    const centerX = (Math.max(...xs) + Math.min(...xs)) / 2;
    const centerY = (Math.max(...ys) + Math.min(...ys)) / 2;
    // Pad, keep a minimum size, and match the frame's portrait aspect.
    width = Math.max(width * 1.8, 240);
    height = Math.max(height * 1.6, width * (MAP.height / MAP.width));
    width = Math.max(width, height * (MAP.width / MAP.height));
    const x = Math.min(Math.max(centerX - width / 2, 0), MAP.width - width);
    const y = Math.min(Math.max(centerY - height / 2, 0), MAP.height - height);
    return `${x.toFixed(0)} ${y.toFixed(0)} ${width.toFixed(0)} ${height.toFixed(0)}`;
  });

  /** Whether the archipelagos are in view, so their labels are not drawn off-frame. */
  readonly fullFrame = computed(() => this.viewBox().startsWith('0 0 '));

  /** Marker size scales with the zoom, so a zoomed-in route does not get fat dots. */
  readonly unit = computed(() => Number(this.viewBox().split(' ')[2]) / 300);

  statusLabel(): string {
    const entry = STATUS_LABELS[this.tracking().status];
    if (!entry) return this.tracking().status;
    return this.lang() === 'vi' ? entry.vi : entry.en;
  }

  when(iso: string | null): string {
    if (!iso) return '';
    return new Intl.DateTimeFormat(this.lang() === 'vi' ? 'vi-VN' : 'en-GB', {
      timeZone: 'Asia/Ho_Chi_Minh',
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(iso));
  }

  day(iso: string | null): string {
    if (!iso) return '';
    return new Intl.DateTimeFormat(this.lang() === 'vi' ? 'vi-VN' : 'en-GB', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    }).format(new Date(`${iso}T00:00:00+07:00`));
  }

  t(vi: string, en: string): string {
    return this.lang() === 'vi' ? vi : en;
  }
}
