import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import EventTable from './EventTable';
import {
  mockEventFrameLost, mockEventGranted, mockEventManual, mockEventNearMiss, mockEventNoPlate,
} from '@/lib/gate-mock-data';

const meta: Meta<typeof EventTable> = {
  title: 'Gate/EventTable',
  component: EventTable,
  tags: ['autodocs'],
  args: { apiBase: '', onOpenAnyway: fn(), onPreview: fn() },
};

export default meta;
type Story = StoryObj<typeof EventTable>;

/** Thumbnails need a logged-in API; here they fall back to the placeholder. */
export const Mixed: Story = {
  args: {
    events: [mockEventGranted, mockEventNearMiss, mockEventNoPlate, mockEventFrameLost, mockEventManual,
             { ...mockEventGranted, id: 106, is_test: true }],
  },
};
