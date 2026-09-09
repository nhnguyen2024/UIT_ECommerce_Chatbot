import { bootstrapApplication } from '@angular/platform-browser';
import { provideZonelessChangeDetection } from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';

import { AppComponent } from './app/app.component';
import { routes } from './app/app.routes';

bootstrapApplication(AppComponent, {
  providers: [
    provideZonelessChangeDetection(),
    provideRouter(routes, withComponentInputBinding()),
    // withFetch uses the Fetch API rather than XMLHttpRequest, which matches
    // how the chat service already streams and avoids shipping two HTTP stacks.
    provideHttpClient(withFetch()),
  ],
}).catch((error: unknown) => console.error(error));
