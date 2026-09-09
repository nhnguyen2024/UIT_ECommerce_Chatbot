import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ChatService, Citation, Product, StreamEvent } from './chat.service';

interface Message {
  role: 'user' | 'assistant';
  text: string;
  citations?: Citation[];
  products?: Product[];
  pending?: boolean;
}

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatComponent {
  private readonly chat = inject(ChatService);
  readonly sessionId = signal<string | null>(localStorage.getItem('atelier-session'));
  readonly messages = signal<Message[]>([
    {
      role: 'assistant',
      text: 'Xin chao. I can help you compare products, understand store policies, or check an order.',
    },
  ]);
  readonly busy = signal(false);
  readonly error = signal('');
  draft = '';

  constructor() {
    const savedSession = this.sessionId();
    if (savedSession) {
      this.chat.getConversation(savedSession).subscribe({
        next: (conversation) => {
          if (conversation.messages.length) {
            this.messages.set(conversation.messages.map((message) => ({
              role: message.role,
              text: message.text,
              citations: message.citations,
              products: message.products,
            })));
          }
        },
        error: () => {
          localStorage.removeItem('atelier-session');
          this.sessionId.set(null);
        },
      });
    }
  }

  send(): void {
    const message = this.draft.trim();
    if (!message || this.busy()) return;

    this.draft = '';
    this.error.set('');
    this.busy.set(true);
    this.messages.update((items) => [
      ...items,
      { role: 'user', text: message },
      { role: 'assistant', text: '', pending: true },
    ]);

    this.chat.stream(message, this.sessionId()).subscribe({
      next: (event) => this.handleEvent(event),
      error: () => {
        this.busy.set(false);
        this.error.set('The assistant is unavailable right now. Please try again.');
        this.removePending();
      },
      complete: () => this.busy.set(false),
    });
  }

  handleKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  reset(): void {
    localStorage.removeItem('atelier-session');
    this.sessionId.set(null);
    this.messages.set([{ role: 'assistant', text: 'A fresh conversation. What are you shopping for today?' }]);
    this.error.set('');
  }

  private handleEvent(event: StreamEvent): void {
    if (event.type === 'session' && event.session_id) {
      this.sessionId.set(event.session_id);
      localStorage.setItem('atelier-session', event.session_id);
    }
    if (event.type === 'text' && event.delta) {
      this.messages.update((items) => this.updateLast(items, (last) => ({
        ...last,
        text: last.text + event.delta,
      })));
    }
    if (event.type === 'products' && event.items) {
      this.messages.update((items) => this.updateLast(items, (last) => ({
        ...last,
        products: event.items as Product[],
      })));
    }
    if (event.type === 'citations' && event.items) {
      this.messages.update((items) => this.updateLast(items, (last) => ({
        ...last,
        citations: event.items as Citation[],
      })));
    }
    if (event.type === 'error') this.error.set(event.message ?? 'The assistant could not complete that request.');
    if (event.type === 'done') {
      this.messages.update((items) => this.updateLast(items, (last) => ({ ...last, text: event.text ?? last.text, pending: false })));
    }
  }

  private updateLast(items: Message[], update: (last: Message) => Message): Message[] {
    if (!items.length) return items;
    return [...items.slice(0, -1), update(items[items.length - 1])];
  }

  private removePending(): void {
    this.messages.update((items) => items.filter((item) => !item.pending));
  }

  formatPrice(value: number | undefined): string {
    return value === undefined ? '' : new Intl.NumberFormat('vi-VN').format(value) + ' VND';
  }
}
