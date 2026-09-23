import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import GateStatusCard from './GateStatusCard';
import { mockGateStatusEsp32Offline, mockGateStatusSimulated } from '@/lib/gate-mock-data';

const meta: Meta<typeof GateStatusCard> = {
  title: 'Gate/GateStatusCard',
  component: GateStatusCard,
  tags: ['autodocs'],
  args: { mode: 'shadow', onCommand: fn(async () => {}) },
  decorators: [(Story) => <div className="max-w-xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof GateStatusCard>;

export const SimulatedOpening: Story = { args: { gate: mockGateStatusSimulated } };
export const Esp32OfflineShadow: Story = { args: { gate: mockGateStatusEsp32Offline } };
export const Esp32Live: Story = { args: { gate: { ...mockGateStatusEsp32Offline, online: true }, mode: 'live' } };
export const NoDecisionYet: Story = { args: { gate: { ...mockGateStatusSimulated, last_event: null, simulator: null, arm_state: 'down' } } };

/** A gate that still only sees one direction. */
export const MissingExitCamera: Story = {
  args: { gate: { ...mockGateStatusSimulated, cameras: [mockGateStatusSimulated.cameras[0]], camera_warning: '1 of 2 cameras assigned. A gate needs one watching vehicles arriving and one watching them leave.' } },
};
