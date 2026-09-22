import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { useState } from 'react';
import RoiPicker from './RoiPicker';
import type { Roi } from '@/lib/gate-api';
import { mockSnapshot } from '@/lib/gate-mock-data';

function Interactive({ initial, editing }: { initial: Roi | null; editing: boolean }) {
  const [roi, setRoi] = useState<Roi | null>(initial);
  return <div className="max-w-xl"><RoiPicker image={mockSnapshot} roi={roi} onChange={setRoi} editing={editing} /></div>;
}

const meta: Meta<typeof Interactive> = {
  title: 'Gate/RoiPicker',
  component: Interactive,
  tags: ['autodocs'],
};

export default meta;
type Story = StoryObj<typeof Interactive>;

export const NoZone: Story = { args: { initial: null, editing: true } };
export const Editing: Story = { args: { initial: { x: 0.35, y: 0.6, w: 0.3, h: 0.25 }, editing: true } };
export const ReadOnly: Story = { args: { initial: { x: 0.35, y: 0.6, w: 0.3, h: 0.25 }, editing: false } };
