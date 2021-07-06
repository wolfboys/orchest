// @ts-check

import React from "react";
import { globalMDCVars } from "@orchest/lib-utils";
import $ from "jquery";

/**
 * @param {Object} props
 * @param {any} props.startNode
 * @param {any} props.endNode
 * @param {any} props.pipelineView
 * @param {any} props.pipelineViewEl
 * @param {string} props.startNodeUUID
 * @param {string} props.endNodeUUID
 * @param {any} props.onClick
 * @param {Number} props.xEnd
 * @param {Number} props.yEnd
 * @param {boolean} props.selected
 * @param {Number} props.scaleFactor
 * @param {any} props.scaleCorrectedPosition
 */

const THEME_SECONDARY = globalMDCVars()["mdcthemesecondary"];

const PipelineConnection = (props) => {
  const connectionHolder = React.useRef(null);

  const curvedHorizontal = function (x1, y1, x2, y2) {
    let line = [];
    let mx = x1 + (x2 - x1) / 2;

    line.push("M", x1, y1);
    line.push("C", mx, y1, mx, y2, x2, y2);

    return line.join(" ");
  };

  const localElementPosition = (el, parentEl) => {
    let position = {};
    position.x = props.scaleCorrectedPosition(
      el.offset().left - $(parentEl).offset().left,
      props.scaleFactor
    );
    position.y = props.scaleCorrectedPosition(
      el.offset().top - $(parentEl).offset().top,
      props.scaleFactor
    );
    return position;
  };

  const nodeCenter = (el, parentEl) => {
    let nodePosition = localElementPosition(el, parentEl);
    nodePosition.x += el.width() / 2;
    nodePosition.y += el.height() / 2;
    return nodePosition;
  };

  const renderSVG = () => {
    if (connectionHolder.current) {
      // initialize SVG
      let svgEl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      let svgPath = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "path"
      );
      svgPath.setAttribute("stroke", "black");
      svgPath.setAttribute("stroke-width", "2");
      svgPath.setAttribute("fill", "none");
      svgPath.setAttribute("id", "path");

      let svgPathClickable = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "path"
      );
      svgPathClickable.setAttribute("stroke", "transparent");
      svgPathClickable.setAttribute("stroke-width", "16");
      svgPathClickable.setAttribute("fill", "none");
      svgPathClickable.setAttribute("id", "path-clickable");

      svgPathClickable.onclick = (e) => {
        if (props.onClick) {
          props.onClick(e, props.startNodeUUID, props.endNodeUUID);
        }
      };
      svgEl.appendChild(svgPath);
      svgEl.appendChild(svgPathClickable);

      // set SVG properties
      const lineHeight = 2;
      const svgPadding = 5;
      const arrowWidth = 7;

      let startNodePosition = nodeCenter(props.startNode, props.pipelineViewEl);
      let x = startNodePosition.x;
      let y = startNodePosition.y;
      let xEnd = props.xEnd !== undefined ? props.xEnd : x;
      let yEnd = props.yEnd !== undefined ? props.yEnd : y;

      let classes = ["connection"];

      // set xEnd and yEnd if endNode is defined
      if (props.endNode) {
        let endNodePosition = nodeCenter(props.endNode, props.pipelineViewEl);
        xEnd = endNodePosition.x;
        yEnd = endNodePosition.y;
      }

      let targetX = xEnd - x;
      let targetY = yEnd - y;

      let xOffset = 0;
      let yOffset = 0;

      if (targetX < 0) {
        xOffset = targetX;
      }

      if (targetX < arrowWidth * 10) {
        classes.push("flipped-horizontal");
      }

      if (targetY < 0) {
        yOffset = targetY;
        classes.push("flipped");
      }

      let styles = {
        transform:
          "translateX(" +
          (x - svgPadding + xOffset) +
          "px) translateY(" +
          (y - svgPadding + yOffset - lineHeight / 2) +
          "px)",
      };

      // update svg poly line
      svgEl.setAttribute("width", Math.abs(targetX) + 2 * svgPadding + "px");
      svgEl.setAttribute("height", Math.abs(targetY) + 2 * svgPadding + "px");

      svgPath.setAttribute(
        "d",
        curvedHorizontal(
          svgPadding - xOffset,
          svgPadding - yOffset,
          svgPadding + targetX - xOffset - arrowWidth,
          svgPadding + targetY - yOffset
        )
      );
      svgPathClickable.setAttribute("d", svgPath.getAttribute("d"));

      if (props.selected) {
        classes.push("selected");
        svgPath.setAttribute("stroke", THEME_SECONDARY);
        svgPath.setAttribute("stroke-width", "3");
      } else {
        svgPath.setAttribute("stroke", "black");
        svgPath.setAttribute("stroke-width", "2");
      }

      connectionHolder.current.className = "";
      connectionHolder.current.classList.add(...classes);
      Object.assign(connectionHolder.current.style, styles);
      connectionHolder.current.replaceChildren(svgEl);
    }
  };

  React.useEffect(() => {
    renderSVG();
  });

  return (
    <div
      data-start-uuid={props.startNodeUUID}
      data-end-uuid={props.endNodeUUID}
      ref={connectionHolder}
    ></div>
  );
};

export default PipelineConnection;
