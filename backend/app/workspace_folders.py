"""Shared workspace folder metadata; never changes document access or blob content."""
import uuid
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ForeignKey, String, select, delete
from sqlalchemy.orm import Mapped, Session, mapped_column
from .db import Base, transaction
from .auth import require_access
from .workspaces import WorkspaceMember, WorkspaceDocument, workspace_access, membership_lock
from .document_library import FolderValue

class WorkspaceFolder(Base):
    __tablename__ = "workspace_folders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("workspace_folders.id"), nullable=True)
    revision: Mapped[str] = mapped_column(String(36))

class WorkspaceFolderDocument(Base):
    __tablename__ = "workspace_folder_documents"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    folder_id: Mapped[str] = mapped_column(ForeignKey("workspace_folders.id"), index=True)

class SharedFolderValue(FolderValue):
    expected_revision: uuid.UUID | None = None

class FolderRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: uuid.UUID

class SharedPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: uuid.UUID
    folder_id: uuid.UUID | None
    expected_folder_id: uuid.UUID | None

def view(row):
    return {"id":row.id,"workspace_id":row.workspace_id,"name":row.name,"parent_id":row.parent_id,"revision":row.revision}

def shared_folders(db,user):
    return [view(row) for row in db.scalars(select(WorkspaceFolder).join(WorkspaceMember,
        WorkspaceMember.workspace_id==WorkspaceFolder.workspace_id).where(WorkspaceMember.user_id==user)
        .order_by(WorkspaceFolder.name,WorkspaceFolder.id))]

def shared_folder_info(db,document_id,workspace_id):
    row=db.get(WorkspaceFolderDocument,document_id)
    folder=db.get(WorkspaceFolder,row.folder_id) if row else None
    if not folder or folder.workspace_id!=workspace_id:
        return {"workspace_folder_id":None,"workspace_folder_path":[]}
    names=[];seen=set();node=folder
    while node and node.id not in seen and node.workspace_id==workspace_id:
        seen.add(node.id);names.insert(0,node.name)
        node=db.get(WorkspaceFolder,node.parent_id) if node.parent_id else None
    return {"workspace_folder_id":folder.id,"workspace_folder_path":names}

def writable(db,workspace_id,user):
    space,member=workspace_access(db,workspace_id,user,lock=True)
    if member.role not in ("manager","editor"):
        raise HTTPException(403,"폴더를 정리하려면 문서함 편집 권한이 필요합니다.")
    return space

def folder_in(db,identity,workspace_id):
    row=db.get(WorkspaceFolder,str(identity),populate_existing=True)
    if not row or row.workspace_id!=workspace_id:raise HTTPException(404,"이 문서함의 폴더를 찾을 수 없습니다.")
    return row

def install_workspace_folders(app):
    engine=app.state.engine
    @app.put("/api/workspaces/{workspace_id}/folders/{folder_id}")
    def put(workspace_id:uuid.UUID,folder_id:uuid.UUID,value:SharedFolderValue,request:Request):
        with transaction(engine) as db:
            membership_lock(db);scope=str(workspace_id);writable(db,scope,request.state.user)
            identity=str(folder_id);parent=str(value.parent_id) if value.parent_id else None
            row=db.get(WorkspaceFolder,identity,populate_existing=True)
            if row and row.workspace_id!=scope:raise HTTPException(404,"이 문서함의 폴더를 찾을 수 없습니다.")
            if row and row.name==value.name and row.parent_id==parent:return view(row)
            if row and row.revision!=str(value.expected_revision):raise HTTPException(409,"폴더가 변경됐습니다. 새로고침 후 다시 시도하세요.")
            cursor=parent;depth=0
            while cursor:
                if cursor==identity or depth>=8:raise HTTPException(409,"폴더는 자기 자신을 포함하거나 8단계를 넘을 수 없습니다.")
                cursor=folder_in(db,cursor,scope).parent_id;depth+=1
            rows=list(db.scalars(select(WorkspaceFolder).where(WorkspaceFolder.workspace_id==scope)))
            def height(node,seen):
                if node in seen:raise HTTPException(409,"폴더 연결을 확인하세요.")
                return max([1]+[1+height(child.id,seen|{node}) for child in rows if child.parent_id==node])
            if depth+height(identity,set())>8:raise HTTPException(409,"폴더는 8단계까지 만들 수 있습니다.")
            if any(f.id!=identity and f.parent_id==parent and f.name.casefold()==value.name.casefold() for f in rows):raise HTTPException(409,"같은 위치에 같은 이름의 폴더가 있습니다.")
            if row:row.name,row.parent_id,row.revision=value.name,parent,str(uuid.uuid4())
            else:
                row=WorkspaceFolder(id=identity,workspace_id=scope,name=value.name,parent_id=parent,revision=str(uuid.uuid4()));db.add(row)
            db.flush();return view(row)
    @app.delete("/api/workspaces/{workspace_id}/folders/{folder_id}")
    def remove(workspace_id:uuid.UUID,folder_id:uuid.UUID,value:FolderRevision,request:Request):
        with transaction(engine) as db:
            membership_lock(db);scope=str(workspace_id);writable(db,scope,request.state.user)
            row=db.get(WorkspaceFolder,str(folder_id),populate_existing=True)
            if not row:return {"ok":True}
            row=folder_in(db,folder_id,scope)
            if row.revision!=str(value.expected_revision):raise HTTPException(409,"폴더가 변경됐습니다. 새로고침 후 다시 시도하세요.")
            if db.scalar(select(WorkspaceFolder.id).where(WorkspaceFolder.parent_id==row.id).limit(1)):raise HTTPException(409,"하위 폴더를 먼저 옮기거나 삭제하세요.")
            db.execute(delete(WorkspaceFolderDocument).where(WorkspaceFolderDocument.folder_id==row.id));db.delete(row)
        return {"ok":True}
    @app.put("/api/documents/{document_id}/workspace-folder")
    def place(document_id:str,value:SharedPlacement,request:Request):
        with transaction(engine) as db:
            membership_lock(db);scope=str(value.workspace_id);writable(db,scope,request.state.user)
            require_access(db,document_id,request.state.user,"editor",lock=True,fresh=True)
            assigned=db.get(WorkspaceDocument,document_id,populate_existing=True)
            if not assigned or assigned.workspace_id!=scope:raise HTTPException(409,"문서함이 변경됐습니다. 최신 목록을 확인하세요.")
            row=db.get(WorkspaceFolderDocument,document_id,populate_existing=True)
            target=folder_in(db,value.folder_id,scope) if value.folder_id else None
            target_id=target.id if target else None
            if (row.folder_id if row else None)==target_id:return {"ok":True}
            if (row.folder_id if row else None)!=(str(value.expected_folder_id) if value.expected_folder_id else None):raise HTTPException(409,"다른 사용자가 폴더를 변경했습니다. 새로고침 후 다시 시도하세요.")
            if target:
                if row:row.folder_id=target.id
                else:db.add(WorkspaceFolderDocument(document_id=document_id,folder_id=target.id))
            elif row:db.delete(row)
        return {"ok":True}
