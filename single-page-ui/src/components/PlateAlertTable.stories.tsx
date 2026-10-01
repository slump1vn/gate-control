import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import PlateAlertTable from './PlateAlertTable';
import { mockPlateAlerts } from '@/lib/gate-mock-data';

const meta: Meta<typeof PlateAlertTable> = {
  title: 'Gate/PlateAlertTable',
  component: PlateAlertTable,
  tags: ['autodocs'],
  args: { alerts: mockPlateAlerts, onEdit: fn(), onToggle: fn(), onDelete: fn() },
};

export default meta;
type Story = StoryObj<typeof PlateAlertTable>;

export const Default: Story = {};
