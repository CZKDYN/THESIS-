'use strict';
const form=document.querySelector('#account-link-form');
const errorBox=document.querySelector('#link-error');
const secret=new URLSearchParams(location.hash.slice(1)).get('token');
history.replaceState(null,'',location.pathname);
async function linkRequest(path,data){
  const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'This link could not be used.');
  return result;
}
function showError(message){errorBox.textContent=message;errorBox.hidden=false;}
(async()=>{
  if(!secret){showError('Open the complete link from your email. It may have expired or already been used.');return;}
  try{const result=await linkRequest('/api/account-links/check',{token:secret,purpose:form.dataset.purpose});
    document.querySelector('#link-description').textContent=`Hello ${result.name}${result.room?' · '+result.room:''}. Choose your own password.`;
    form.elements.token.value=secret;form.hidden=false;
  }catch(error){showError(error.message+' Contact your administrator for a new invitation.');}
})();
form.addEventListener('submit',async event=>{
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;errorBox.hidden=true;
  try{const result=await linkRequest(location.pathname,Object.fromEntries(new FormData(form)));form.elements.token.value='';location.assign(result.redirect);}
  catch(error){showError(error.message);button.disabled=false;}
});
