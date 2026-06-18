import { VideoDetail } from "@/components/video/video-detail";

export default async function VideoDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <VideoDetail id={id} />;
}
