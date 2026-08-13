import React from 'react';
import {AbsoluteFill, Composition, interpolate, Sequence, useCurrentFrame, Video} from 'remotion';

type Props = {videoUrl: string; logoUrl?: string; headline: string; cta: string};

const Ad: React.FC<Props> = ({videoUrl, logoUrl, headline, cta}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [660, 690], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <AbsoluteFill style={{backgroundColor: '#111'}}>
    {videoUrl ? <Video src={videoUrl} /> : <AbsoluteFill style={{background: 'linear-gradient(160deg,#171813,#303522)'}} />}
    <Sequence from={660} durationInFrames={60}>
      <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 110, opacity}}>
        <div style={{width: '82%', padding: '30px 34px', borderRadius: 28, background: 'rgba(10,14,12,.82)', color: 'white', textAlign: 'center', fontFamily: 'Microsoft YaHei'}}>
          {logoUrl && <img src={logoUrl} style={{height: 70, objectFit: 'contain'}} />}
          <div style={{fontSize: 44, fontWeight: 700}}>{headline}</div>
          <div style={{fontSize: 25, marginTop: 12, color: '#deebb4'}}>{cta}</div>
        </div>
      </AbsoluteFill>
    </Sequence>
  </AbsoluteFill>;
};

export const Root: React.FC = () => <Composition
  id="FrameFlowAd" component={Ad} durationInFrames={720} fps={24} width={720} height={1280}
  defaultProps={{videoUrl: '', headline: '让陪伴，自然发生', cta: '了解更多'}}
/>;
