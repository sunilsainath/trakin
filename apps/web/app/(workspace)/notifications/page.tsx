'use client'

/**
 * `/notifications` is the same surface as `/feed`: the app shell links here from
 * the bell, and the feed already renders the notification inbox. Rendering the
 * one component avoids two lists drifting apart.
 */
export { default } from '../feed/page'