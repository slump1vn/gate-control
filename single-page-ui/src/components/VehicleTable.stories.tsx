import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import VehicleTable from './VehicleTable';
import { mockVehicles } from '@/lib/gate-mock-data';

const meta: Meta<typeof VehicleTable> = {
  title: 'Gate/VehicleTable',
  component: VehicleTable,
  tags: ['autodocs'],
  args: { vehicles: mockVehicles, onEdit: fn(), onDeactivate: fn(), onReactivate: fn() },
};

export default meta;
type Story = StoryObj<typeof VehicleTable>;

export const Default: Story = {};
