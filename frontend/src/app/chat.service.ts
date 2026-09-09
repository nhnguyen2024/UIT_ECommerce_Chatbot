import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

export interface Citation {
  source_id: string;
  kind: string;
}

export interface Product {
  sku: string;
  name_vi?: string;
  name_en?: string;
  brand?: string;
  price?: number;
  sale_price?: number | null;
  rating?: number;
  stock?: number;
}

export interface StreamEvent {
  type: string;
  delta?: string;
  text?: string;
  session_id?: string;
  citations?: Citation[];
  items?: unknown[];
  message?: string;
}

export interface StoredConversation {
  session_id: string;
  messages: Array<{
    role: 'user' | 'assistant';
    text: string;
    citations?: Citation[];
    products?: Product[];
  }>;
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly endpoint = '/api/chat/stream';

  getConversation(sessionId: string): Observable<StoredConversation> {
    return new Observable<StoredConversation>((subscriber) => {
      fetch(`/api/chat/${encodeURIComponent(sessionId)}`)
        .then(async (response) => {
          if (!response.ok) throw new Error(`Conversation load failed (${response.status})`);
          subscriber.next(await response.json() as StoredConversation);
          subscriber.complete();
        })
        .catch((error: unknown) => subscriber.error(error));
    });
  }

  stream(message: string, sessionId: string | null): Observable<StreamEvent> {
    return new Observable<StreamEvent>((subscriber) => {
      const controller = new AbortController();

      fetch(this.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ message, session_id: sessionId }),
        signal: controller.signal,
      }).then(async (response) => {
        if (!response.ok || !response.body) {
          throw new Error(`Chat request failed (${response.status})`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          const frames = buffer.split('\n\n');
          buffer = frames.pop() ?? '';

          for (const frame of frames) {
            const data = frame.split('\n')
              .find((line) => line.startsWith('data:'))
              ?.slice(5).trim();
            if (data) subscriber.next(JSON.parse(data) as StreamEvent);
          }
          if (done) break;
        }
        subscriber.complete();
      }).catch((error: unknown) => {
        if (!controller.signal.aborted) subscriber.error(error);
      });

      return () => controller.abort();
    });
  }
}
